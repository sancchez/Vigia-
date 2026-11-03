"""Reporte final DETERMINISTA (sin LLM) — entregable a un cliente real.

Por qué existe
--------------
`agents/reporteria.py` compila el reporte con Claude. Sin
`ANTHROPIC_API_KEY` (o con la API caída) el documento final era:

    [Reporte narrativo pendiente — LLM no disponible: ...]
    {  ...el JSON crudo del estado del pipeline...  }

No miente — es honesto sobre la degradación — pero es inentregable: un
cliente que paga recibe un volcado de estructuras de datos. Este módulo
produce, con los MISMOS datos y sin ningún modelo de lenguaje, un documento
legible: postura, hallazgos agrupados por severidad con evidencia concreta
(URI, parámetro, fragmento observado) y la remediación recomendada.

El texto declara explícitamente que se generó sin análisis de LLM — eso es
información honesta para el lector, no una excusa para entregar menos.

Mismo patrón que `tools/report_export.py::construir_markdown_ejecutivo` y
`tools/takedown.py`: función pura de formato, sin red, sin base de datos,
sin LLM. No recalcula severidades ni urgencias: las toma tal como las
dejaron `agents/verificacion.py` (vía `tools/triage.py`) y
`agents/priorizacion.py`.
"""

from __future__ import annotations

from tools.triage import (
    FALSO_POSITIVO_DECLARADO,
    ORDEN_SEVERIDAD,
    ORDEN_URGENCIA,
    evidencia_corta,
    ordenar_por_riesgo,
)

_TITULO_SEVERIDAD: dict[str, str] = {
    "critical": "Riesgo crítico",
    "high": "Riesgo alto",
    "medium": "Riesgo medio",
    "low": "Riesgo bajo",
    "info": "Informativos",
}

_NOTA_SEVERIDAD: dict[str, str] = {
    "critical": "Requieren acción inmediata.",
    "high": "Requieren acción prioritaria.",
    "medium": "Deben planificarse en el ciclo de trabajo actual.",
    "low": "Mejoras de robustez, sin urgencia.",
    "info": (
        "NO son vulnerabilidades. Se listan por transparencia del alcance revisado: "
        "no requieren acción y no deben leerse con la misma urgencia que lo de arriba."
    ),
}

_ETIQUETA_CLASE: dict[str, str] = {
    "vulnerabilidad_explotable": "vulnerabilidad explotable",
    "amenaza_externa": "amenaza externa a la marca",
    "higiene_configuracion": "higiene de configuración",
    "informativo": "informativo",
}

_AVISO_SIN_LLM = (
    "> **Cómo se generó este documento.** Este reporte se compiló de forma "
    "determinista, sin análisis de un modelo de lenguaje (no había uno disponible "
    "en esta corrida). Todo lo que sigue proviene directamente de la salida de las "
    "herramientas de escaneo y de reglas de clasificación explícitas y auditables: "
    "no hay interpretación ni redacción generada. La contrapartida es que no hay "
    "narrativa de contexto de negocio; los datos, la evidencia y la remediación "
    "recomendada están completos."
)


def _severidad(hallazgo: dict) -> str:
    return str(hallazgo.get("severidad") or "medium").strip().lower()


def _linea_evidencia(hallazgo: dict) -> list[str]:
    lineas: list[str] = []
    endpoint = str(hallazgo.get("endpoint") or "").strip()
    if endpoint:
        lineas.append(f"  - Ubicación: `{endpoint}`")
    parametro = str(hallazgo.get("parametro") or "").strip()
    if parametro:
        lineas.append(f"  - Parámetro afectado: `{parametro}`")
    evidencia = evidencia_corta(hallazgo.get("evidencia"))
    if evidencia:
        lineas.append(f"  - Evidencia observada: `{evidencia}`")
    instancias = hallazgo.get("instancias")
    if isinstance(instancias, int) and instancias > 1:
        lineas.append(f"  - Instancias afectadas: {instancias}")
    herramienta = str(hallazgo.get("herramienta") or "").strip()
    if herramienta:
        lineas.append(f"  - Detectado por: {herramienta}")
    clase = str(hallazgo.get("clase") or "").strip()
    if clase:
        lineas.append(f"  - Tipo de hallazgo: {_ETIQUETA_CLASE.get(clase, clase)}")
    urgencia = str(hallazgo.get("urgencia_negocio") or "").strip()
    if urgencia:
        lineas.append(f"  - Urgencia de negocio: {urgencia}")
    if hallazgo.get("exploit_publico"):
        cves = hallazgo.get("cves_confirmados") or hallazgo.get("cves_detectados") or []
        lineas.append(
            "  - **Existe exploit público conocido**"
            + (f" para {', '.join(cves)}" if cves else "")
            + " (Exploit-DB)."
        )
    return lineas


def _seccion_resumen(validos: list[dict], descartados: list[dict]) -> list[str]:
    lineas = ["## Resumen de postura", ""]
    if not validos:
        lineas.append(
            "El escaneo no devolvió hallazgos activos sobre el alcance autorizado. "
            "Eso significa que las herramientas ejecutadas no detectaron nada en esta "
            "corrida, no que el sistema sea invulnerable."
        )
        lineas.append("")
    else:
        conteo: dict[str, int] = {}
        for h in validos:
            sev = _severidad(h)
            conteo[sev] = conteo.get(sev, 0) + 1
        accionables = sum(n for sev, n in conteo.items() if sev != "info")
        lineas.append(
            f"**{len(validos)} hallazgos** sobre el alcance autorizado, de los cuales "
            f"**{accionables} requieren acción** y {conteo.get('info', 0)} son informativos."
        )
        lineas.append("")
        for sev in sorted(conteo, key=lambda s: ORDEN_SEVERIDAD.get(s, 99)):
            lineas.append(f"- {_TITULO_SEVERIDAD.get(sev, sev)}: {conteo[sev]}")
        lineas.append("")
        con_exploit = sum(1 for h in validos if h.get("exploit_publico"))
        if con_exploit:
            lineas.append(
                f"{con_exploit} de esos hallazgos tienen exploit público conocido en "
                "Exploit-DB, lo que los vuelve prioritarios."
            )
        else:
            lineas.append(
                "Ninguno de estos hallazgos tiene un exploit público asociado. Eso NO "
                "los invalida: la mayoría de los problemas de configuración (cabeceras "
                "de seguridad ausentes, CORS abierto, cookies sin flags) no tienen CVE "
                "por naturaleza y aun así son reales y explotables en contexto."
            )
        lineas.append("")
    if descartados:
        lineas.append(
            f"Se descartaron {len(descartados)} alertas que la propia herramienta de "
            "escaneo marcó como falso positivo. No se cuentan arriba ni se presentan "
            "como hallazgos; quedan listadas al final para que la corrida sea auditable."
        )
        lineas.append("")
    return lineas


def _seccion_hallazgos(validos: list[dict]) -> list[str]:
    lineas = ["## Hallazgos", ""]
    if not validos:
        lineas.append("Sin hallazgos que detallar en esta corrida.")
        lineas.append("")
        return lineas

    por_severidad: dict[str, list[dict]] = {}
    for h in ordenar_por_riesgo(validos):
        por_severidad.setdefault(_severidad(h), []).append(h)

    for sev in sorted(por_severidad, key=lambda s: ORDEN_SEVERIDAD.get(s, 99)):
        grupo = por_severidad[sev]
        lineas.append(f"### {_TITULO_SEVERIDAD.get(sev, sev)} ({len(grupo)})")
        nota = _NOTA_SEVERIDAD.get(sev)
        if nota:
            lineas.append("")
            lineas.append(nota)
        lineas.append("")
        for h in grupo:
            lineas.append(f"- **{h.get('tipo') or 'hallazgo sin nombre'}**")
            lineas.extend(_linea_evidencia(h))
            lineas.append("")
    return lineas


def _seccion_remediacion(prioritized: list[dict], remediations: list[dict]) -> list[str]:
    lineas = ["## Remediación recomendada", ""]
    if not remediations:
        lineas.append(
            "No hay remediaciones asociadas a esta corrida (no hubo hallazgos que "
            "remediar)."
        )
        lineas.append("")
        return lineas

    # `hallazgo_indice` indexa `prioritized_findings` (ver agents/remediacion.py).
    for item in sorted(
        remediations,
        key=lambda r: ORDEN_URGENCIA.get(str(r.get("urgencia_negocio", "")).lower(), 9),
    ):
        idx = item.get("hallazgo_indice")
        hallazgo = (
            prioritized[idx]
            if isinstance(idx, int) and 0 <= idx < len(prioritized)
            else {}
        )
        titulo = item.get("tipo") or hallazgo.get("tipo") or f"Hallazgo #{idx}"
        lineas.append(f"### {titulo}")
        lineas.append("")
        lineas.append(str(item.get("remediacion") or "").strip())
        lineas.append("")
    return lineas


def _seccion_descartados(descartados: list[dict]) -> list[str]:
    if not descartados:
        return []
    lineas = ["## Alertas descartadas (falsos positivos declarados por la herramienta)", ""]
    lineas.append(
        "La herramienta de escaneo reportó estas alertas con confianza 0, que en su "
        "propio vocabulario significa \"falso positivo\". No se presentan como "
        "hallazgos ni se cuentan en la postura; se listan solo para que quede "
        "constancia de que se vieron y se descartaron con criterio explícito."
    )
    lineas.append("")
    for h in descartados:
        endpoint = str(h.get("endpoint") or "").strip()
        lineas.append(
            f"- {h.get('tipo') or 'alerta sin nombre'}"
            + (f" (`{endpoint}`)" if endpoint else "")
        )
    lineas.append("")
    return lineas


def _seccion_marca(antisuplantacion: list[dict]) -> list[str]:
    if not antisuplantacion:
        return []
    lineas = ["## Exposición de marca", ""]
    lineas.append(
        f"Se recolectaron {len(antisuplantacion)} señales del módulo de "
        "anti-suplantación en esta corrida. El detalle accionable por dominio "
        "(scoring de infraestructura, captura y borrador de takedown) está en el "
        "panel de Protección de marca."
    )
    lineas.append("")
    return lineas


def _seccion_alcance(datos: dict, validos: list[dict]) -> list[str]:
    lineas = ["## Alcance y limitaciones", ""]
    recon = datos.get("recon_findings") or []
    lineas.append(
        f"- Objetivo evaluado: `{datos.get('target') or 'no declarado'}`."
    )
    lineas.append(f"- Señales de reconocimiento pasivo recolectadas: {len(recon)}.")
    herramientas = sorted(
        {str(h.get("herramienta")) for h in validos if h.get("herramienta")}
    )
    if herramientas:
        lineas.append(f"- Herramientas que produjeron hallazgos: {', '.join(herramientas)}.")
    lineas.append(
        "- La ausencia de un hallazgo no prueba la ausencia del problema: un escaneo "
        "cubre lo que sus reglas saben mirar, en el momento en que corrió."
    )
    lineas.append(
        "- Las severidades son las que reportó cada herramienta, normalizadas a la "
        "escala del sistema. No se elevó ninguna: un hallazgo informativo se presenta "
        "como informativo."
    )
    lineas.append("")
    return lineas


def construir_reporte_determinista(datos: dict) -> str:
    """Compila el reporte final en markdown a partir del estado del pipeline.

    `datos` acepta directamente el `PipelineState` (o el `resumen_datos` que
    arma `agents/reporteria.py`): usa `target`, `recon_findings`,
    `verified_findings`, `prioritized_findings`, `remediations` y
    `antisuplantacion_findings`.
    """
    verificados = list(datos.get("verified_findings") or [])
    descartados = [
        h for h in verificados if h.get("estado_validez") == FALSO_POSITIVO_DECLARADO
    ]
    validos = [
        h for h in verificados if h.get("estado_validez") != FALSO_POSITIVO_DECLARADO
    ]
    prioritized = list(datos.get("prioritized_findings") or [])
    # Priorización ya trae urgencia por hallazgo; si corrió, es la fuente más
    # completa (mismo hallazgo + `urgencia_negocio`). Si no corrió, se usan los
    # verificados tal cual — nunca se pierde un hallazgo por esa diferencia.
    detalle = prioritized if prioritized else validos

    lineas: list[str] = [
        f"# Reporte de seguridad — {datos.get('target') or 'objetivo no declarado'}",
        "",
        _AVISO_SIN_LLM,
        "",
    ]
    lineas.extend(_seccion_resumen(detalle, descartados))
    lineas.extend(_seccion_hallazgos(detalle))
    lineas.extend(
        _seccion_remediacion(prioritized, list(datos.get("remediations") or []))
    )
    lineas.extend(_seccion_marca(list(datos.get("antisuplantacion_findings") or [])))
    lineas.extend(_seccion_descartados(descartados))
    lineas.extend(_seccion_alcance(datos, detalle))

    return "\n".join(lineas).strip() + "\n"
