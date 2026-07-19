"""Agente de Cumplimiento Normativo (Ley 2573 de 2026 / ISO 27001) — Item 6 del backlog (HANDOFF.md).

Distinto de `agents/reporteria.py` (reporte técnico de UN scan, generado
dentro del grafo LangGraph): este módulo NO es un nodo del grafo. Se invoca
bajo demanda (`GET /reports/cumplimiento`, ver `api/main.py`) sobre el
HISTORIAL completo de `findings` ya guardado en la base de datos para un
tenant — potencialmente varios scans a lo largo del tiempo — porque el
gancho de venta es justo eso: trazabilidad y evidencia acumulada, no una
foto de un solo escaneo.

## Open-core: este archivo es la capa PÚBLICA/community

La taxonomía real de categorización (13 categorías afinadas contra datos
reales), el mapeo específico a controles ISO/IEC 27001 Anexo A, el mapeo a
las obligaciones de la Ley 2573 de 2026, y el prompt de redacción del
reporte narrativo son la IP más diferenciada de este proyecto — viven en el
paquete privado `vigia_core_private` (fuera de este repo, nunca publicado,
ver `docs/open-core.md`).

Este módulo intenta importar esa capa real (`vigia_core_private.cumplimiento`)
y, si el paquete no está instalado (`ImportError` — el caso normal para
cualquiera que clone este repo público), cae a una taxonomía GENÉRICA de 3
categorías amplias sin mapeo específico a controles, definida más abajo
(`_CATEGORIA_MAPEO_FALLBACK`, `_categorizar_hallazgo_fallback`,
`_SYSTEM_PROMPT_FALLBACK`). El endpoint sigue funcionando de punta a punta
en ambos casos — solo cambia qué tan específico es el reporte.

## Por qué hace falta una capa de categorización (motivo real, no supuesto)

`findings.tipo` en la base de datos vale `"desconocido"` para casi todo
hallazgo que viene de ZAP/Nuclei (bug real encontrado al probar este módulo
contra datos reales: `api/main.py::scan()` hace
`hallazgo.get("tipo") or hallazgo.get("type") or "desconocido"`, pero el
hallazgo verificado nunca tiene esas claves en el nivel superior — solo
dentro de `raw`). Mapear directo por `tipo` dejaría casi todo en un cajón
genérico sin valor de venta. Por eso la función de categorización mira
dentro de `raw_json` (nombre de alerta de ZAP, tags/severity de Nuclei,
cweid, regla de Semgrep, CVE de Trivy/Grype) para inferir una categoría
útil, y solo cae en `sin_clasificar` cuando de verdad no hay señal
reconocible. Documentado también en `docs/cumplimiento.md`.

## Limitación conocida: anti-suplantación no persiste en `findings`

Ver `docs/cumplimiento.md` — brecha ya cerrada, `api/main.py` aplana
`antisuplantacion_findings` a filas `findings` reales.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from ._llm import LLMNoDisponibleError, call_claude

AGENTE = "cumplimiento"

# ---------------------------------------------------------------------------
# Capa real (privada) vs. fallback genérico (público/community).
# ---------------------------------------------------------------------------

try:
    from vigia_core_private.cumplimiento import (
        CATEGORIA_MAPEO,
        ISO27001_ADVERTENCIA,
        LEY2573_ADVERTENCIA_ALCANCE,
        LEY2573_OBLIGACIONES,
        SYSTEM_PROMPT,
        categorizar_hallazgo as _categorizar_hallazgo,
    )

    _MODO_CUMPLIMIENTO = "privado"
except ImportError:
    _MODO_CUMPLIMIENTO = "fallback_publico"

    # --- Fallback genérico: 3 categorías amplias, sin mapeo ISO/Ley 2573
    # específico por categoría. Deliberadamente menos útil que la capa
    # privada real — sirve para que el repo público sea 100% funcional y
    # demostrable sin revelar la taxonomía/mapeo diferenciado del producto.
    LEY2573_OBLIGACIONES = {
        "seguridad_digital_razonable": (
            "Adoptar medidas de seguridad digital razonables y poder "
            "demostrarlas si se exige (obligación general de la Ley 2573 "
            "de 2026 — ver un abogado para el desglose detallado)."
        ),
    }

    LEY2573_ADVERTENCIA_ALCANCE = (
        "Esta es la versión community/pública del reporte de cumplimiento. "
        "No incluye el mapeo detallado de obligaciones ni la advertencia de "
        "alcance legal específica de Vigia — instale el paquete privado "
        "para el mapeo completo. En cualquier caso, ningún reporte "
        "automatizado reemplaza la revisión de un abogado."
    )

    ISO27001_ADVERTENCIA = (
        "Esta versión community no incluye mapeo a controles específicos "
        "del Anexo A de ISO/IEC 27001:2022. Instale el paquete privado para "
        "el mapeo detallado, y en cualquier caso valide con un auditor "
        "certificado antes de usar como evidencia formal."
    )

    CATEGORIA_MAPEO = {
        "hallazgo_tecnico": {
            "nombre": "Hallazgo técnico de seguridad",
            "iso27001": ["(mapeo detallado disponible en el paquete privado)"],
            "ley2573_obligaciones": ["seguridad_digital_razonable"],
            "explicacion": (
                "Hallazgo detectado por el motor de escaneo (ZAP/Nuclei/"
                "Semgrep/Trivy/Grype). La versión community no distingue "
                "subcategorías ni controles específicos."
            ),
        },
        "suplantacion_marca": {
            "nombre": "Posible suplantación de marca o dominio",
            "iso27001": ["(mapeo detallado disponible en el paquete privado)"],
            "ley2573_obligaciones": ["seguridad_digital_razonable"],
            "explicacion": (
                "Señal de anti-suplantación (dominio variante o perfil de "
                "red social). La versión community no distingue el tipo "
                "exacto de señal."
            ),
        },
        "sin_clasificar": {
            "nombre": "Hallazgo sin categoría reconocida",
            "iso27001": ["(mapeo detallado disponible en el paquete privado)"],
            "ley2573_obligaciones": ["seguridad_digital_razonable"],
            "explicacion": "No se pudo determinar el tipo de señal.",
        },
    }

    def _cargar_raw_fallback(finding: dict) -> dict:
        raw = finding.get("raw_json")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = {}
        return raw if isinstance(raw, dict) else {}

    def _categorizar_hallazgo(finding: dict) -> str:
        """Versión genérica: distingue solo 3 cajones amplios (técnico,
        suplantación de marca, sin clasificar) por herramienta/tipo, sin la
        taxonomía fina de 13 categorías ni las reglas de palabras clave
        afinadas del paquete privado."""
        tipo = (finding.get("tipo") or "").lower()
        raw = _cargar_raw_fallback(finding)
        herramienta = str(raw.get("herramienta") or "").lower()

        if tipo in ("dominio_variante_certstream", "dominio_variante", "perfil_red_social") or herramienta in (
            "dnstwist",
            "sherlock",
        ):
            return "suplantacion_marca"
        if herramienta in ("zap", "nuclei", "semgrep", "trivy", "grype") or tipo not in ("", "desconocido"):
            return "hallazgo_tecnico"
        return "sin_clasificar"

    SYSTEM_PROMPT = (
        "Eres el redactor del reporte de cumplimiento normativo de una "
        "plataforma de gestión de superficie de ataque. Recibes un resumen "
        "ya clasificado de hallazgos técnicos (categoría amplia, sin "
        "mapeo detallado a controles) y debes redactar un reporte breve y "
        "claro para el dueño de una pyme: qué se encontró, por qué importa, "
        "y qué hacer primero. Esta es la versión community — no tiene el "
        "detalle de mapeo a ISO 27001 ni a la Ley 2573 de 2026 de la "
        "versión completa de Vigia. Nunca inventes numeración legal ni "
        "controles específicos que no se te dieron."
    )


# ---------------------------------------------------------------------------
# Categorización de un hallazgo individual — helpers compartidos (no son
# proprietarios en sí mismos, solo extraen texto buscable; la lista de
# categorías y las reglas de palabras clave sí lo son, y viven arriba).
# ---------------------------------------------------------------------------


def _cargar_raw(finding: dict) -> dict:
    raw = finding.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return raw


def _texto_buscable(finding: dict, raw: dict) -> str:
    """Concatena señales textuales ESTRUCTURADAS del hallazgo en minúsculas.

    A propósito NO incluye `desc`/`description` (el texto libre que ZAP/
    Nuclei escriben explicando el hallazgo): esos campos suelen mencionar
    de pasada otras categorías de vulnerabilidad como contexto, causando
    falsos positivos de categorización (ver `docs/cumplimiento.md`).
    """
    nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    info = nested.get("info") if isinstance(nested.get("info"), dict) else {}
    partes = [
        str(finding.get("tipo") or ""),
        str(raw.get("herramienta") or ""),
        str(nested.get("alert") or nested.get("name") or ""),
        str(nested.get("cweid") or ""),
        str(nested.get("template-id") or nested.get("check_id") or nested.get("id") or ""),
        str(info.get("name") or ""),
        " ".join(str(t) for t in (info.get("tags") or nested.get("tags") or []) if t),
    ]
    return " ".join(partes).lower()


# ---------------------------------------------------------------------------
# Construcción del reporte.
# ---------------------------------------------------------------------------


def _resumir_por_categoria(findings: list[dict]) -> list[dict]:
    agrupado: dict[str, list[dict]] = defaultdict(list)
    for finding in findings:
        categoria = _categorizar_hallazgo(finding)
        agrupado[categoria].append(finding)

    resumen = []
    for categoria, items in sorted(agrupado.items(), key=lambda kv: -len(kv[1])):
        meta = CATEGORIA_MAPEO[categoria]
        resumen.append(
            {
                "categoria": categoria,
                "nombre": meta["nombre"],
                "cantidad": len(items),
                "iso27001": meta["iso27001"],
                "ley2573_obligaciones": [
                    {"id": oid, "texto": LEY2573_OBLIGACIONES[oid]}
                    for oid in meta["ley2573_obligaciones"]
                ],
                "explicacion": meta["explicacion"],
                "ejemplos": [
                    {
                        "id": item.get("id"),
                        "endpoint": item.get("endpoint"),
                        "severidad": item.get("severidad"),
                        "scan_id": item.get("scan_id"),
                    }
                    for item in items[:5]
                ],
            }
        )
    return resumen


def _cobertura_ley2573(resumen: list[dict]) -> dict[str, dict]:
    """Para cada obligación documentada, qué categorías de hallazgo aportan
    evidencia (aunque sea evidencia de una brecha, no de cumplimiento —
    encontrar y documentar la brecha ES la trazabilidad que pide la
    obligación de conservar evidencia)."""
    cobertura = {oid: {"texto": texto, "categorias_relacionadas": []} for oid, texto in LEY2573_OBLIGACIONES.items()}
    for entrada in resumen:
        for obligacion in entrada["ley2573_obligaciones"]:
            cobertura[obligacion["id"]]["categorias_relacionadas"].append(entrada["categoria"])
    # La obligación de trazabilidad siempre tiene evidencia estructural: el
    # propio reporte, con timestamp, es el artefacto de trazabilidad.
    for oid in cobertura:
        if "trazabilidad" in oid:
            cobertura[oid]["categorias_relacionadas"] = sorted(
                set(cobertura[oid]["categorias_relacionadas"]) | {"*todas*"}
            )
    return cobertura


def _reporte_fallback(resumen: list[dict], contexto_negocio: str, motivo: str) -> str:
    partes = [
        "# Reporte de Cumplimiento (Ley 2573 de 2026 / ISO 27001) — generado sin LLM",
        f"[Narrativa pendiente — LLM no disponible: {motivo}. Datos estructurados abajo son reales, "
        "solo falta la redacción en lenguaje llano.]",
        "",
        f"Contexto de negocio: {contexto_negocio or '(no provisto)'}",
        "",
        LEY2573_ADVERTENCIA_ALCANCE,
        "",
        ISO27001_ADVERTENCIA,
        "",
        "## Hallazgos por categoría",
    ]
    for entrada in resumen:
        partes.append(
            f"\n### {entrada['nombre']} ({entrada['cantidad']} hallazgo(s))\n"
            f"- Controles ISO 27001: {', '.join(entrada['iso27001'])}\n"
            f"- Obligaciones Ley 2573: "
            f"{', '.join(o['texto'] for o in entrada['ley2573_obligaciones'])}\n"
            f"- {entrada['explicacion']}"
        )
    return "\n".join(partes)


def generar_reporte_cumplimiento(
    findings: list[dict],
    contexto_negocio: str = "",
) -> dict:
    """Genera el reporte de cumplimiento a partir del historial de `findings` de un tenant.

    Args:
        findings: filas de la tabla `findings` (como dict, incluyendo
            `raw_json`) para el tenant, típicamente `SELECT * FROM findings
            WHERE tenant_id = ? ORDER BY created_at DESC`. Puede combinar
            varios scans a través del tiempo a propósito.
        contexto_negocio: mismo campo que usa `agents/priorizacion.py`, para
            que el reporte pueda mencionar qué hace la empresa.

    Returns:
        dict con `reporte_markdown`, `resumen_por_categoria`,
        `cobertura_ley2573`, `advertencia_alcance_legal`,
        `advertencia_iso27001`, `modo` (`"privado"` o `"fallback_publico"`,
        para que quien consuma el reporte sepa qué tan detallado es), y
        `trace`.
    """
    generado_en = datetime.now(timezone.utc).isoformat()

    if not findings:
        return {
            "reporte_markdown": (
                "# Reporte de Cumplimiento (Ley 2573 de 2026 / ISO 27001)\n\n"
                "No hay hallazgos registrados todavía para este tenant. Corre "
                "un escaneo (`POST /scan`) antes de generar este reporte — "
                "sin hallazgos no hay evidencia que documentar.\n\n"
                f"{LEY2573_ADVERTENCIA_ALCANCE}"
            ),
            "resumen_por_categoria": [],
            "cobertura_ley2573": _cobertura_ley2573([]),
            "advertencia_alcance_legal": LEY2573_ADVERTENCIA_ALCANCE,
            "advertencia_iso27001": ISO27001_ADVERTENCIA,
            "modo": _MODO_CUMPLIMIENTO,
            "generado_en": generado_en,
            "trace": [
                {
                    "agente": AGENTE,
                    "accion": "generar_reporte_cumplimiento",
                    "resultado": "sin hallazgos, reporte vacío",
                    "timestamp": generado_en,
                }
            ],
        }

    resumen = _resumir_por_categoria(findings)
    cobertura = _cobertura_ley2573(resumen)

    entrada_llm = {
        "contexto_negocio": contexto_negocio or "(sin contexto de negocio adicional)",
        "total_hallazgos": len(findings),
        "resumen_por_categoria": resumen,
        "cobertura_ley2573": cobertura,
        "advertencia_alcance_legal": LEY2573_ADVERTENCIA_ALCANCE,
        "advertencia_iso27001": ISO27001_ADVERTENCIA,
    }

    try:
        reporte_markdown = call_claude(
            SYSTEM_PROMPT,
            "Datos clasificados para el reporte de cumplimiento (JSON):\n"
            + json.dumps(entrada_llm, ensure_ascii=False, indent=2, default=str),
        )
        resultado_trace = f"reporte generado con LLM sobre {len(findings)} hallazgos (modo={_MODO_CUMPLIMIENTO})"
    except LLMNoDisponibleError as exc:
        reporte_markdown = _reporte_fallback(resumen, contexto_negocio, str(exc))
        resultado_trace = f"LLM no disponible, reporte fallback determinista: {exc}"

    return {
        "reporte_markdown": reporte_markdown,
        "resumen_por_categoria": resumen,
        "cobertura_ley2573": cobertura,
        "advertencia_alcance_legal": LEY2573_ADVERTENCIA_ALCANCE,
        "advertencia_iso27001": ISO27001_ADVERTENCIA,
        "modo": _MODO_CUMPLIMIENTO,
        "generado_en": generado_en,
        "trace": [
            {
                "agente": AGENTE,
                "accion": "generar_reporte_cumplimiento",
                "resultado": resultado_trace,
                "timestamp": generado_en,
            }
        ],
    }
