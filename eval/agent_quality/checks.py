"""Checks deterministas reusables para los evals de calidad del fallback
público (Fase 7). Ver `eval/agent_quality/__init__.py` para el alcance.

Cada función `validar_*` devuelve una lista de strings con los errores
encontrados (lista vacía == pasó). Se usan tanto desde
`eval/agent_quality/run_agent_quality.py` (script standalone, mismo patrón
que `eval/run_eval.py`) como desde `tests/test_agent_quality_fallback.py`
(pytest, checks deterministas -- LLM-as-judge queda fuera de la suite por
defecto, ver design.md).
"""

from __future__ import annotations

import importlib.util

# Menor número = más urgente. Mismo orden que `Dashboard.tsx::SEV_ORDEN` en
# el frontend, para que "orden por severidad técnica" signifique lo mismo en
# todo el proyecto.
_RANGO_SEVERIDAD = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# Frases que el SYSTEM_PROMPT de fallback de remediación PROHÍBE
# explícitamente ("Nunca prometas que el arreglo es 100% infalible") --
# encontrar una es indicio de sobre-promesa/alucinación de certeza.
_FRASES_SOBRE_PROMESA = (
    "100% infalible",
    "100% seguro",
    "totalmente infalible",
    "garantizado al 100%",
    "elimina completamente el riesgo",
)


def es_entorno_fallback_publico() -> bool:
    """True si `vigia_core_private` NO está instalado en esta máquina --
    el caso normal del repo público (ver docs/open-core.md). Los evals de
    calidad de esta carpeta están diseñados para pasar en ese caso; si el
    paquete privado SÍ está instalado, los tests que dependen de esto
    deben saltarse (mismo patrón que `tests/test_cumplimiento_fallback.py`)."""
    return importlib.util.find_spec("vigia_core_private") is None


def _rango(severidad: str) -> int:
    return _RANGO_SEVERIDAD.get(str(severidad).strip().lower(), 99)


# ---------------------------------------------------------------------------
# Priorización
# ---------------------------------------------------------------------------

_CAMPOS_PRIORIZACION = {"urgencia_negocio", "justificacion"}
# Fase 4: se agrega "informativa" -- un hallazgo con `riskcode 0` de ZAP llega
# al reporte, pero NO con la misma urgencia que un riesgo real (ver
# `tools/triage.py::_URGENCIA_POR_SEVERIDAD`). "sin_evaluar"/"sin_parsear"
# quedan en el conjunto por compatibilidad con salidas ya persistidas: el
# fallback de `agents/priorizacion.py` ya no las produce (ahora cae a la
# urgencia determinista, que sí es accionable).
_URGENCIAS_VALIDAS = {
    "alta", "media", "baja", "informativa", "sin_evaluar", "sin_parsear",
}


def validar_esquema_priorizacion(salida: list[dict]) -> list[str]:
    """Cada item de `prioritized_findings` (salida de
    `agents/priorizacion.py::node()`) debe conservar `urgencia_negocio` y
    `justificacion` (además de los campos originales del hallazgo) -- forma
    exigida por spec.md: "`{hallazgo_id, urgencia_negocio, justificacion}`"
    (acá `hallazgo_id` corresponde al hallazgo original ya fusionado, no un
    campo separado -- node() lo fusiona con `**confirmados[idx]`)."""
    errores = []
    for i, item in enumerate(salida):
        faltantes = _CAMPOS_PRIORIZACION - item.keys()
        if faltantes:
            errores.append(f"item[{i}]: faltan campos {sorted(faltantes)}")
            continue
        if item["urgencia_negocio"] not in _URGENCIAS_VALIDAS:
            errores.append(
                f"item[{i}]: urgencia_negocio={item['urgencia_negocio']!r} no es uno de {sorted(_URGENCIAS_VALIDAS)}"
            )
        if not isinstance(item["justificacion"], str) or not item["justificacion"].strip():
            errores.append(f"item[{i}]: justificacion vacía o no es texto")
    return errores


def validar_orden_por_severidad(salida: list[dict]) -> list[str]:
    """La propiedad declarada del fallback de priorización (spec.md: "orden
    por severidad técnica") -- ninguna entrada debe ser MÁS urgente (rango
    menor) que la entrada anterior en la lista de salida."""
    errores = []
    for i in range(1, len(salida)):
        anterior = _rango(salida[i - 1].get("severidad", ""))
        actual = _rango(salida[i].get("severidad", ""))
        if actual < anterior:
            errores.append(
                f"item[{i}] (severidad={salida[i].get('severidad')!r}) es más urgente que "
                f"item[{i - 1}] (severidad={salida[i - 1].get('severidad')!r}) -- orden no respeta severidad"
            )
    return errores


# ---------------------------------------------------------------------------
# Remediación
# ---------------------------------------------------------------------------

def validar_esquema_remediacion(salida: list[dict]) -> list[str]:
    """Forma de `agents/remediacion.py::node()`: cada item debe tener
    `hallazgo_indice` (int) y `remediacion` (texto no vacío)."""
    errores = []
    for i, item in enumerate(salida):
        if "hallazgo_indice" not in item or not isinstance(item["hallazgo_indice"], int):
            errores.append(f"item[{i}]: falta hallazgo_indice entero")
        if not isinstance(item.get("remediacion"), str) or not item["remediacion"].strip():
            errores.append(f"item[{i}]: remediacion vacía o no es texto")
    return errores


def validar_no_sobre_promesa(salida: list[dict]) -> list[str]:
    """Ausencia de marcadores de alucinación/sobre-promesa (spec.md: "y
    ausencia de marcadores de alucinación / formato inválido") -- el
    SYSTEM_PROMPT de fallback prohíbe explícitamente prometer que un
    arreglo es 100% infalible."""
    errores = []
    for i, item in enumerate(salida):
        texto = str(item.get("remediacion", "")).lower()
        for frase in _FRASES_SOBRE_PROMESA:
            if frase in texto:
                errores.append(f"item[{i}]: contiene frase de sobre-promesa prohibida: {frase!r}")
    return errores


# ---------------------------------------------------------------------------
# Revisión de código IA
# ---------------------------------------------------------------------------

_CAMPOS_REVISION_IA = {
    "archivo",
    "linea_aprox",
    "categoria",
    "severidad",
    "descripcion",
    "evidencia",
    "recomendacion",
}


def validar_esquema_revision_ia(hallazgos: list[dict]) -> list[str]:
    """Forma del `INSTRUCCION_FORMATO` de fallback en `agents/revision_ia.py`
    (una sola categoría `logica_de_negocio` en modo fallback_publico)."""
    errores = []
    for i, h in enumerate(hallazgos):
        faltantes = _CAMPOS_REVISION_IA - h.keys()
        if faltantes:
            errores.append(f"hallazgo[{i}]: faltan campos {sorted(faltantes)}")
        if not isinstance(h.get("evidencia"), str) or not h["evidencia"].strip():
            errores.append(f"hallazgo[{i}]: evidencia vacía o no es texto")
    return errores


def _buscar_contenido(archivos: dict[str, str], archivo: str | None) -> str | None:
    """Empareja `archivo` contra las claves de `archivos` (rutas reales
    devueltas por `revisar_codigo`), tolerando que uno sea ruta absoluta y
    el otro relativa/con separadores distintos -- el LLM (o el fixture
    canned de este eval) puede repetir la ruta tal cual se la dieron, que
    no necesariamente coincide carácter por carácter con `str(Path(...))`."""
    if archivo is None:
        return None
    if archivo in archivos:
        return archivos[archivo]
    import os as _os

    objetivo = _os.path.normcase(_os.path.basename(archivo))
    for ruta, contenido in archivos.items():
        if _os.path.normcase(_os.path.basename(ruta)) == objetivo:
            return contenido
    return None


def validar_no_alucinacion_evidencia(hallazgos: list[dict], archivos: dict[str, str]) -> list[str]:
    """El SYSTEM_PROMPT de fallback prohíbe "inventar un número de línea o
    una cita de código que no esté literalmente en el archivo" -- este check
    verifica que `evidencia` sea de verdad una cita LITERAL del contenido
    real del archivo (`archivos`: {ruta: contenido})."""
    errores = []
    for i, h in enumerate(hallazgos):
        archivo = h.get("archivo")
        evidencia = h.get("evidencia", "")
        contenido = _buscar_contenido(archivos, archivo)
        if contenido is None:
            errores.append(f"hallazgo[{i}]: archivo {archivo!r} no está entre los archivos revisados")
            continue
        if evidencia and evidencia not in contenido:
            errores.append(
                f"hallazgo[{i}]: evidencia {evidencia!r} no es una cita literal de {archivo!r} -- posible alucinación"
            )
    return errores
