"""Memoria histórica entre corridas — Fase 3 (agentización).

Hasta esta fase, cada corrida del grafo empezaba con amnesia total: el mismo
hallazgo reportado en cinco scans seguidos se narraba las cinco veces como si
fuera nuevo, y un hallazgo que el cliente ya había cerrado o aceptado como
riesgo volvía a aparecer con la misma urgencia. Este módulo lee lo que la base
de datos YA guarda (`scans`, `findings`, `finding_transiciones`) y lo convierte
en un párrafo de contexto que se inyecta al prompt de Priorización y Reportería.

Decisiones de diseño, explícitas:

- **Texto, no JSON.** La salida se concatena a un system prompt; un LLM lee
  mejor prosa estructurada que un volcado crudo, y así no gastamos contexto en
  llaves y comillas.
- **Sin historial => string vacío.** Un tenant nuevo (el estado en el que quedó
  la base tras la Fase 2) es el caso NORMAL, no un error. Los consumidores
  tratan `""` como "no agregues nada al prompt", así que el comportamiento es
  idéntico al de antes de esta fase.
- **Sin `LIKE`.** `db/connection.py` traduce `?` -> `%s` con un `str.replace`
  ingenuo para Postgres; un `%` literal dentro del SQL rompería esa traducción.
  Por eso el filtrado por texto (notas que mencionan un falso positivo) se hace
  en Python sobre las filas ya traídas, no en el `WHERE`.
- **"Falso positivo" no es un estado del schema.** El CHECK de `findings.estado`
  no tiene un valor `falso_positivo`; lo más cercano que existe de verdad es
  `aceptado_riesgo` (el cliente vio el hallazgo y decidió no arreglarlo) y la
  `nota` de una transición donde alguien escribió que era un falso positivo.
  Se reportan esas dos señales por lo que son, sin inventar una tercera.
- **Todo falla en silencio hacia `""`.** Un error de base de datos en la capa
  de memoria no puede tumbar un scan: la memoria es una mejora de calidad del
  reporte, no una dependencia dura del pipeline.
"""

from __future__ import annotations

import logging

from db.connection import dict_from_row, get_conn

logger = logging.getLogger("vigia.memoria")

# Cuántas filas traer de cada eje. Suficiente para dar contexto real sin
# convertir el prompt en un volcado de la base.
MAX_SCANS_PREVIOS = 5
MAX_REINCIDENTES = 10
MAX_TRANSICIONES = 10
MAX_FALSOS_POSITIVOS = 10

# Marcadores que el equipo del cliente escribe a mano en la nota de una
# transición cuando descarta un hallazgo. Se buscan en minúsculas, sin acentos
# obligatorios, sobre la nota ya traída a Python (ver docstring: nada de LIKE).
_MARCADORES_FALSO_POSITIVO = ("falso positivo", "falso_positivo", "false positive")

_ESTADO_ACEPTADO_RIESGO = "aceptado_riesgo"


def _clave_hallazgo(fila: dict) -> tuple[str, str]:
    """Identidad de un hallazgo entre scans distintos: qué es + dónde está."""
    return (str(fila.get("tipo") or "desconocido"), str(fila.get("endpoint") or ""))


def _describir_hallazgo(tipo: str, endpoint: str) -> str:
    return f"{tipo} en {endpoint}" if endpoint else tipo


def _scans_previos(conn, tenant_id: str, target: str) -> list[dict]:
    filas = conn.execute(
        """
        SELECT id, estado, created_at, completed_at
        FROM scans
        WHERE tenant_id = ? AND target = ?
        ORDER BY created_at DESC
        """,
        (tenant_id, target),
    ).fetchall()
    return [dict_from_row(f) or {} for f in filas]


def _findings_del_target(conn, tenant_id: str, target: str) -> list[dict]:
    filas = conn.execute(
        """
        SELECT f.id, f.scan_id, f.tipo, f.endpoint, f.severidad, f.estado,
               f.clase, f.created_at
        FROM findings f
        JOIN scans s ON s.id = f.scan_id
        WHERE f.tenant_id = ? AND s.target = ?
        ORDER BY f.created_at ASC
        """,
        (tenant_id, target),
    ).fetchall()
    return [dict_from_row(f) or {} for f in filas]


def _transiciones_recientes(conn, tenant_id: str, target: str) -> list[dict]:
    filas = conn.execute(
        """
        SELECT t.estado_anterior, t.estado_nuevo, t.nota, t.created_at,
               f.tipo, f.endpoint
        FROM finding_transiciones t
        JOIN findings f ON f.id = t.finding_id
        JOIN scans s ON s.id = f.scan_id
        WHERE t.tenant_id = ? AND s.target = ?
        ORDER BY t.created_at DESC
        """,
        (tenant_id, target),
    ).fetchall()
    return [dict_from_row(f) or {} for f in filas]


def _bloque_scans(scans: list[dict]) -> list[str]:
    if len(scans) <= 1:
        return []
    lineas = [
        f"- Este objetivo ya fue escaneado {len(scans)} veces antes. "
        f"Scans más recientes (del más nuevo al más viejo): "
        + ", ".join(
            f"{s.get('created_at', 'fecha desconocida')} ({s.get('estado', 'sin estado')})"
            for s in scans[:MAX_SCANS_PREVIOS]
        )
        + "."
    ]
    return lineas


def _bloque_reincidentes(findings: list[dict]) -> list[str]:
    """Hallazgos que aparecieron en MÁS DE UN scan: el dato que más pesa."""
    por_clave: dict[tuple[str, str], set] = {}
    severidad_por_clave: dict[tuple[str, str], str] = {}
    estado_por_clave: dict[tuple[str, str], str] = {}
    for fila in findings:
        clave = _clave_hallazgo(fila)
        por_clave.setdefault(clave, set()).add(fila.get("scan_id"))
        severidad_por_clave[clave] = str(fila.get("severidad") or "info")
        estado_por_clave[clave] = str(fila.get("estado") or "descubierto")

    reincidentes = sorted(
        ((clave, len(scans)) for clave, scans in por_clave.items() if len(scans) > 1),
        key=lambda par: par[1],
        reverse=True,
    )
    if not reincidentes:
        return []

    lineas = ["- Hallazgos REINCIDENTES (ya reportados en scans anteriores y aún abiertos o reabiertos):"]
    for (tipo, endpoint), n_scans in reincidentes[:MAX_REINCIDENTES]:
        clave = (tipo, endpoint)
        lineas.append(
            f"  * {_describir_hallazgo(tipo, endpoint)} "
            f"[severidad {severidad_por_clave[clave]}, estado actual "
            f"{estado_por_clave[clave]}] — visto en {n_scans} scans distintos."
        )
    return lineas


def _bloque_transiciones(transiciones: list[dict]) -> list[str]:
    if not transiciones:
        return []
    lineas = ["- Cambios de estado recientes registrados por el equipo del cliente:"]
    for t in transiciones[:MAX_TRANSICIONES]:
        anterior = t.get("estado_anterior") or "(nuevo)"
        nuevo = t.get("estado_nuevo") or "(sin cambio)"
        nota = (t.get("nota") or "").strip()
        sufijo = f' — nota: "{nota}"' if nota else ""
        lineas.append(
            f"  * {t.get('created_at', 'fecha desconocida')}: "
            f"{_describir_hallazgo(str(t.get('tipo') or ''), str(t.get('endpoint') or ''))} "
            f"pasó de {anterior} a {nuevo}{sufijo}"
        )
    return lineas


def _bloque_descartados(findings: list[dict], transiciones: list[dict]) -> list[str]:
    """Lo que el cliente ya dijo que NO es un problema para él."""
    descartados: list[str] = []

    for fila in findings:
        if str(fila.get("estado") or "") == _ESTADO_ACEPTADO_RIESGO:
            tipo, endpoint = _clave_hallazgo(fila)
            descartados.append(
                f"  * {_describir_hallazgo(tipo, endpoint)} — el cliente lo marcó "
                f"como riesgo aceptado."
            )

    for t in transiciones:
        nota = (t.get("nota") or "").strip()
        nota_lower = nota.lower()
        if any(m in nota_lower for m in _MARCADORES_FALSO_POSITIVO):
            descriptor = _describir_hallazgo(
                str(t.get("tipo") or ""), str(t.get("endpoint") or "")
            )
            descartados.append(
                f'  * {descriptor} — el cliente lo reportó como falso positivo: "{nota}"'
            )

    # `dict.fromkeys` deduplica conservando el orden (un mismo hallazgo puede
    # estar aceptado como riesgo Y tener una nota de falso positivo).
    descartados = list(dict.fromkeys(descartados))
    if not descartados:
        return []
    return [
        "- Hallazgos que el cliente YA descartó (no los presentes como urgentes ni "
        "como novedad; si reaparecen, dilo explícitamente como reaparición de algo "
        "ya evaluado):",
        *descartados[:MAX_FALSOS_POSITIVOS],
    ]


def contexto_historico(tenant_id: str, target: str) -> str:
    """Contexto histórico de este tenant sobre este objetivo, como texto para prompt.

    Devuelve `""` si no hay historial relevante (tenant/objetivo nuevo, o
    cualquier fallo de lectura) — el caller lo trata como "no agregues nada".
    """
    if not tenant_id or not target:
        return ""

    conn = None
    try:
        conn = get_conn()
        scans = _scans_previos(conn, tenant_id, target)
        if not scans:
            return ""
        findings = _findings_del_target(conn, tenant_id, target)
        transiciones = _transiciones_recientes(conn, tenant_id, target)
    except Exception as exc:  # pragma: no cover - degradación defensiva
        logger.warning("No se pudo leer el contexto histórico de %s: %s", target, exc)
        return ""
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    secciones: list[str] = []
    secciones += _bloque_scans(scans)
    secciones += _bloque_reincidentes(findings)
    secciones += _bloque_transiciones(transiciones)
    secciones += _bloque_descartados(findings, transiciones)

    if not secciones:
        return ""

    encabezado = (
        f"HISTORIAL PREVIO DE ESTE CLIENTE SOBRE {target} "
        "(datos reales de la base, no suposiciones):"
    )
    return "\n".join([encabezado, *secciones])
