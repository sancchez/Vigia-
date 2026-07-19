"""Agente de Revisión de Código con IA — ítem transversal de HANDOFF.md
("la idea concreta pendiente es un agente de revisión de código con IA que
complemente a Semgrep... Semgrep encuentra patrones sintácticos, un modelo
Claude puede encontrar fallas de lógica de negocio que un analizador
estático no ve"). Ese ítem decía explícitamente "no empezar esto antes de
que Semgrep esté conectado (item 3)" — Semgrep ya está wireado en
`agents/escaneo.py` vía `scope.codigo_paths` (commit `d8e58f3`), así que
este módulo ya no está bloqueado.

## Qué SÍ hace (y qué NO)

Semgrep (y herramientas SAST en general) encuentran patrones sintácticos:
concatenación de SQL, secretos hardcodeados, funciones criptográficas
débiles. No pueden razonar sobre INTENCIÓN: si un chequeo de autorización
compara el campo correcto, si una consulta multi-tenant de verdad aísla al
tenant en TODOS sus caminos de código, si un precio que debería calcularse
en el servidor se está tomando tal cual del cliente. Ese es el hueco que
este módulo llena, apoyándose en `agents/_llm.py::call_claude()` (mismo
helper que usan `priorizacion.py`, `remediacion.py`, `reporteria.py` y
`cumplimiento.py`).

El `SYSTEM_PROMPT` de abajo es deliberadamente estrecho: enumera 5
categorías concretas de fallas de lógica y prohíbe explícitamente reportar
lo que Semgrep ya cubre (secretos, SQL sin parametrizar, criptografía
débil, cabeceras faltantes). Un prompt genérico ("revisa este código en
busca de bugs") habría producido una lista que se solapa con Semgrep y
diluye el valor de tener dos capas — la sección 3.1 del plan ya distingue
capas por tipo de objetivo, y aquí se distingue por tipo de RAZONAMIENTO
(sintáctico vs. semántico/de negocio), no solo por el dato de entrada.

## Decisión de diseño 1: ¿Semgrep-aware o standalone?

Semgrep-aware, pero opcional (`semgrep_findings=None` funciona igual).
Razón: sin ver lo que Semgrep ya marcó, este agente no tiene forma de saber
qué NO repetir, y perdería la oportunidad más valiosa que le da tener
ambas capas — razonar sobre si un patrón que Semgrep marcó como "posible
SQL injection" es de verdad explotable en ESTE código concreto (¿el
parámetro llega de un input de usuario o de una constante interna?), algo
que un analizador sintáctico no puede decidir por sí mismo. `_resumir_semgrep()`
reduce esos hallazgos a una lista compacta (regla, archivo, línea, mensaje)
en vez de pasar el JSON crudo completo — así el LLM tiene contexto útil sin
que el prompt se infle con el `raw_json` completo de cada alerta.

## Decisión de diseño 2: ¿nodo del grafo o función bajo demanda?

Función bajo demanda, mismo patrón que `agents/cumplimiento.py` — NO se
agregó a `orchestrator/graph.py`. Razones concretas:

1. **Costo y frecuencia no encajan con el ciclo recurrente.** El escaneo
   pasivo automático (`api/scheduler.py`, cada N horas sobre todos los
   dominios activos de todos los tenants) ya corre con IA real en
   Priorización/Remediación/Reportería, y HANDOFF.md Item 1 documenta que
   la latencia de `claude -p` ya es un problema real (una llamada tardó
   >180s). Agregar N llamadas más por ciclo (una por archivo/lote de
   código) a un flujo que corre sin que nadie lo pida activamente
   multiplicaría ese riesgo de timeout sin que el cliente lo haya
   solicitado.
2. **`scope.codigo_paths` normalmente está vacío.** La mayoría de targets
   del pipeline son URLs de producción del cliente (`scope.dominios`), no
   rutas de código fuente en el filesystem de quien corre Vigia — el caso
   de uso real de revisión de código es "el cliente nos dio acceso a su
   repo, revísenlo", una tarea puntual bajo demanda, no parte de la
   vigilancia continua de una URL pública.
3. **Necesita leer contenido real de archivos, no solo hallazgos.** A
   diferencia de todo nodo actual del grafo (que reciben/producen `dict`s
   de hallazgos ya extraídos), este módulo hace I/O de filesystem sobre
   código fuente potencialmente sensible del cliente — mezclar esa
   responsabilidad dentro de un nodo del `PipelineState` (pensado para
   fluir hallazgos, no contenido de archivos) sería forzarlo. Igual que
   `cumplimiento.py` opera sobre el HISTORIAL de `findings` en vez de un
   solo `PipelineState`, este módulo opera sobre archivos reales bajo
   demanda.
4. **Implicación de privacidad/seguridad distinta y real, no solo de
   diseño de grafo:** enviar código fuente real a un LLM (API de Anthropic
   o `claude -p` vía CLI) es un tipo de exposición de datos distinto a
   correr Semgrep (100% local, nada sale de la máquina). Por eso
   `revisar_codigo()` exige DOS flags separados, no uno: `autorizacion_firmada`
   (la misma puerta de todo el proyecto: hay permiso para tocar/analizar
   este objetivo) Y `envio_codigo_a_llm_autorizado` (el cliente sabe y
   acepta específicamente que el CONTENIDO de su código se envíe a un LLM
   de terceros). Son consentimientos distintos — un cliente puede autorizar
   escanear su dominio sin necesariamente autorizar que su código fuente
   propietario viaje a la API de Anthropic. Ningún nodo existente del grafo
   tenía que modelar esta distinción porque ninguno envía contenido de
   archivo crudo a un LLM; este si.

Si en el futuro el volumen de uso bajo demanda justifica automatizarlo
(ej. "cada vez que el cliente sube código nuevo"), un endpoint explícito
(`POST /reports/revision-ia`, mismo patrón que `GET /reports/cumplimiento`
en `api/main.py`) es el siguiente paso natural — no un nodo del grafo.

## Limitaciones conocidas, documentadas a propósito

- Los límites de tamaño (`_MAX_ARCHIVOS`, `_MAX_BYTES_POR_ARCHIVO`,
  `_MAX_BYTES_TOTAL`) existen porque `call_claude()` es una sola llamada
  con todo el contenido en el mensaje de usuario — no hay chunking. Un
  repo grande completo NO cabe de una sola pasada; este módulo está
  pensado para revisar rutas/módulos puntuales que el llamador elige con
  criterio (ej. "el módulo de auth", "el endpoint de pagos"), igual que
  `scope.codigo_paths` ya se usa para Semgrep sobre rutas específicas, no
  necesariamente el repo entero de una vez.
- El LLM puede alucinar. `_INSTRUCCION_FORMATO` pide explícitamente `[]`
  cuando no hay hallazgos reales y prohíbe inventar números de línea o
  citas de código que no estén literalmente en el archivo — pero no hay
  una verificación determinista de que la cita exista de verdad en el
  archivo (a diferencia de `agents/verificacion.py`, que sí es
  determinista). Ver `docs/` / `eval/live_run_report.md` para los
  resultados reales de la corrida de prueba de este módulo, incluyendo si
  apareció algún falso positivo.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ._llm import LLMNoDisponibleError, call_claude

AGENTE = "revision_ia"

# ---------------------------------------------------------------------------
# Selección de archivos.
# ---------------------------------------------------------------------------

_EXTENSIONES_SOPORTADAS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rb", ".php", ".cs", ".kt"
}

_DIRS_EXCLUIDOS = {
    "node_modules", ".git", "venv", ".venv", "__pycache__", "dist", "build",
    ".mypy_cache", ".pytest_cache", "vendor", ".eggs", "eggs-info",
}

# `call_claude()` no trocea el contenido — todo va en un solo mensaje de
# usuario. Estos límites existen para no reventar el contexto/timeout de la
# CLI (ver HANDOFF.md Item 1) y para forzar al llamador a elegir rutas con
# criterio en vez de apuntar esto a un repo entero.
_MAX_ARCHIVOS = 25
_MAX_BYTES_POR_ARCHIVO = 40_000
_MAX_BYTES_TOTAL = 120_000


def _recolectar_archivos(codigo_paths: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Lee el contenido real de los archivos bajo `codigo_paths`.

    Acepta tanto archivos individuales como directorios (mismo criterio que
    `tools/scan.py::run_semgrep`, que recibe `path: str` y acepta ambos).
    Devuelve `(archivos, omitidos)`: `archivos` es `[(ruta, contenido)]` y
    `omitidos` es una lista de strings explicando qué se dejó fuera y por
    qué (nunca falla silenciosamente).
    """
    archivos: list[tuple[str, str]] = []
    omitidos: list[str] = []
    total_bytes = 0

    for ruta_str in codigo_paths:
        ruta = Path(ruta_str)
        if not ruta.exists():
            omitidos.append(f"{ruta_str} (la ruta no existe)")
            continue

        if ruta.is_file():
            candidatos = [ruta]
        else:
            candidatos = sorted(
                p
                for p in ruta.rglob("*")
                if p.is_file()
                and p.suffix in _EXTENSIONES_SOPORTADAS
                and not any(parte in _DIRS_EXCLUIDOS for parte in p.parts)
            )

        for archivo in candidatos:
            if len(archivos) >= _MAX_ARCHIVOS:
                omitidos.append(
                    f"{archivo} (omitido: se alcanzó el límite de {_MAX_ARCHIVOS} archivos por corrida)"
                )
                continue
            try:
                contenido = archivo.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                omitidos.append(f"{archivo} (no se pudo leer: {exc})")
                continue

            if len(contenido) > _MAX_BYTES_POR_ARCHIVO:
                contenido = (
                    contenido[:_MAX_BYTES_POR_ARCHIVO]
                    + "\n... [truncado — archivo más largo que el límite por archivo]"
                )
            if total_bytes + len(contenido) > _MAX_BYTES_TOTAL:
                omitidos.append(
                    f"{archivo} (omitido: se alcanzó el límite total de contexto de esta corrida)"
                )
                continue

            total_bytes += len(contenido)
            archivos.append((str(archivo), contenido))

    return archivos, omitidos


def _resumir_semgrep(semgrep_findings: list[dict]) -> str:
    """Reduce hallazgos de Semgrep (forma de `agents/escaneo.py::node()`,
    `{"objetivo":..., "herramienta": "semgrep", "raw": {...}}`) a un digest
    compacto: regla, archivo, línea, mensaje. Nunca se pasa el `raw_json`
    completo — eso infla el prompt sin agregar señal nueva."""
    relevantes = [
        f for f in semgrep_findings if str(f.get("herramienta") or "").lower() == "semgrep"
    ]
    if not relevantes:
        return (
            "(No se pasaron hallazgos de Semgrep a esta revisión — revisa "
            "SOLO las 5 categorías de lógica de negocio, sin contexto adicional.)"
        )

    lineas = ["Hallazgos YA reportados por Semgrep (contexto, NO los repitas tal cual):"]
    for f in relevantes[:30]:
        raw = f.get("raw") or {}
        if not isinstance(raw, dict):
            continue
        check_id = raw.get("check_id", "?")
        ruta = raw.get("path", f.get("objetivo", "?"))
        linea = (raw.get("start") or {}).get("line", "?")
        mensaje = str((raw.get("extra") or {}).get("message", ""))[:200]
        lineas.append(f"- [{check_id}] {ruta}:{linea} — {mensaje}")
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Prompt — Open-core: el prompt real (con las 5 categorías finas de lógica
# de negocio) vive en `vigia_core_private.revision_ia` (ver
# docs/open-core.md). Si el paquete no está instalado, se usa un prompt
# genérico de una sola categoría amplia — el módulo sigue siendo funcional,
# solo menos específico en la clasificación de lo que encuentra.
# ---------------------------------------------------------------------------

try:
    from vigia_core_private.revision_ia import (
        CATEGORIAS as _CATEGORIAS,
        INSTRUCCION_FORMATO as _INSTRUCCION_FORMATO,
        SYSTEM_PROMPT,
    )
except ImportError:
    _CATEGORIAS = ("logica_de_negocio",)

    SYSTEM_PROMPT = (
        "Eres un revisor de código senior especializado en fallas de "
        "LÓGICA DE NEGOCIO que un analizador estático como Semgrep NO puede "
        "detectar porque no son patrones sintácticos — requieren entender "
        "qué se supone que hace el código y compararlo con lo que "
        "realmente hace (ej. checks de autorización que comparan el campo "
        "equivocado, datos que deberían validarse en el servidor pero se "
        "toman tal cual del cliente, pasos de un flujo que pueden "
        "saltarse).\n\n"
        "NO reportes lo que ya cubre Semgrep: secretos hardcodeados, SQL "
        "sin parametrizar, criptografía débil, cabeceras faltantes. Si un "
        "archivo no tiene ningún hallazgo real, responde con lista vacía — "
        "no inventes uno.\n\n"
        "Nunca inventes un número de línea o una cita de código que no "
        "esté literalmente en el archivo dado.\n\n"
        "Esta es la versión community: no distingue subcategorías (control "
        "de acceso, aislamiento multi-tenant, confianza en input del "
        "cliente, lógica de flujo, gestión de sesión/token) — todo cae en "
        "una sola categoría `logica_de_negocio`. La versión privada de "
        "Vigia sí hace ese desglose fino."
    )

    _INSTRUCCION_FORMATO = (
        "\n\nResponde ÚNICAMENTE con JSON válido (sin texto extra, sin markdown "
        "fences), una lista de objetos con esta forma exacta:\n"
        '[{"archivo": "ruta/al/archivo", "linea_aprox": <entero o null>, '
        '"categoria": "logica_de_negocio", '
        '"severidad": "alta|media|baja", '
        '"descripcion": "qué está mal, en términos de lógica de negocio, no de '
        'sintaxis", "evidencia": "cita corta y textual del código que sustenta '
        'el hallazgo", "recomendacion": "qué cambiar", '
        '"relacionado_con_semgrep": "check_id de Semgrep si esto reinterpreta '
        'un hallazgo suyo, o null"}]\n'
        "Si no encuentras ningún hallazgo real, responde con una lista vacía "
        "`[]` — no inventes hallazgos para tener algo que reportar."
    )


def _parsear_hallazgos(texto: str) -> list[dict] | None:
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
    try:
        data = json.loads(texto)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # El LLM a veces antepone texto explicativo antes del bloque JSON pese a
    # la instrucción explícita de "responde ÚNICAMENTE con JSON" (encontrado
    # real probando este módulo contra `agents/cumplimiento.py`: la respuesta
    # empezó con "Basándome en el análisis del archivo..." antes del ```json).
    # Intento de recuperación: aislar el primer '[' hasta el último ']' del
    # texto completo antes de rendirse.
    inicio = texto.find("[")
    fin = texto.rfind("]")
    if inicio != -1 and fin != -1 and fin > inicio:
        try:
            data = json.loads(texto[inicio : fin + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _trace(accion: str, resultado: str) -> dict:
    """Mismo formato que `orchestrator.state.make_trace_event`, pero este
    módulo no importa `PipelineState`/ese helper a propósito — no es un
    nodo del grafo (ver docstring, "decisión de diseño 2"), igual que
    `agents/cumplimiento.py` construye su propio `trace` manualmente."""
    return {
        "agente": AGENTE,
        "accion": accion,
        "resultado": resultado,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def revisar_codigo(
    codigo_paths: list[str],
    autorizacion_firmada: bool,
    envio_codigo_a_llm_autorizado: bool = False,
    semgrep_findings: list[dict] | None = None,
    contexto_negocio: str = "",
) -> dict:
    """Revisa `codigo_paths` en busca de fallas de LÓGICA DE NEGOCIO (ver
    `SYSTEM_PROMPT`) usando `call_claude()`. No reemplaza a Semgrep — lo
    complementa (ver docstring del módulo).

    Args:
        codigo_paths: archivos o directorios de código fuente ya
            autorizados (mismo campo que `Scope.codigo_paths`,
            `orchestrator/state.py`).
        autorizacion_firmada: misma puerta que el resto del proyecto — hay
            permiso para analizar este objetivo. Defensa en profundidad
            explícita, igual que `agents/escaneo.py::node()`: este módulo
            vuelve a comprobarla aunque se llame fuera del flujo normal.
        envio_codigo_a_llm_autorizado: consentimiento SEPARADO de
            `autorizacion_firmada` — el cliente acepta específicamente que
            el CONTENIDO de su código fuente se envíe a un LLM de terceros
            (API de Anthropic o `claude -p`). Ver "decisión de diseño 2",
            punto 4, en el docstring del módulo para el porqué de que sean
            dos flags distintos.
        semgrep_findings: opcional, hallazgos crudos de
            `agents/escaneo.py::node()` (forma
            `{"herramienta": "semgrep", "raw": {...}}`) para que el LLM
            razone sobre exploitabilidad/contexto en vez de repetirlos.
        contexto_negocio: mismo campo que usan `priorizacion.py`/
            `cumplimiento.py`.

    Returns:
        dict con `hallazgos_logica`, `archivos_revisados`,
        `archivos_omitidos`, `contexto_semgrep_usado` (bool),
        `generado_en`, `trace`.
    """
    generado_en = datetime.now(timezone.utc).isoformat()

    if not autorizacion_firmada:
        return {
            "hallazgos_logica": [],
            "archivos_revisados": [],
            "archivos_omitidos": [],
            "contexto_semgrep_usado": False,
            "generado_en": generado_en,
            "trace": [
                _trace(
                    "rechazar_revision_sin_autorizacion",
                    "tarea rechazada: autorizacion_firmada no es true, no se leyó ningún archivo",
                )
            ],
        }

    if not envio_codigo_a_llm_autorizado:
        return {
            "hallazgos_logica": [],
            "archivos_revisados": [],
            "archivos_omitidos": [],
            "contexto_semgrep_usado": False,
            "generado_en": generado_en,
            "trace": [
                _trace(
                    "rechazar_envio_codigo_a_llm",
                    "tarea rechazada: envio_codigo_a_llm_autorizado no es true — "
                    "autorizacion_firmada autoriza analizar el objetivo, pero enviar "
                    "el CONTENIDO del código fuente a un LLM de terceros requiere "
                    "consentimiento explícito adicional, que no se dio",
                )
            ],
        }

    if not codigo_paths:
        return {
            "hallazgos_logica": [],
            "archivos_revisados": [],
            "archivos_omitidos": [],
            "contexto_semgrep_usado": False,
            "generado_en": generado_en,
            "trace": [_trace("revisar_codigo", "sin codigo_paths, nada que revisar")],
        }

    archivos, omitidos = _recolectar_archivos(codigo_paths)
    if not archivos:
        return {
            "hallazgos_logica": [],
            "archivos_revisados": [],
            "archivos_omitidos": omitidos,
            "contexto_semgrep_usado": False,
            "generado_en": generado_en,
            "trace": [
                _trace(
                    "revisar_codigo",
                    f"ningún archivo legible bajo {codigo_paths}; omitidos: {omitidos}",
                )
            ],
        }

    digest_semgrep = _resumir_semgrep(semgrep_findings or [])
    bloques_codigo = "\n\n".join(
        f"--- archivo: {ruta} ---\n{contenido}" for ruta, contenido in archivos
    )
    entrada = (
        f"Contexto de negocio del cliente: {contexto_negocio or '(no provisto)'}\n\n"
        f"{digest_semgrep}\n\n"
        f"Archivos a revisar ({len(archivos)}):\n\n{bloques_codigo}"
        f"{_INSTRUCCION_FORMATO}"
    )

    try:
        respuesta = call_claude(SYSTEM_PROMPT, entrada)
    except LLMNoDisponibleError as exc:
        return {
            "hallazgos_logica": [],
            "archivos_revisados": [ruta for ruta, _ in archivos],
            "archivos_omitidos": omitidos,
            "contexto_semgrep_usado": bool(semgrep_findings),
            "generado_en": generado_en,
            "trace": [_trace("revisar_codigo", f"LLM no disponible, sin fallback determinista posible: {exc}")],
        }

    hallazgos = _parsear_hallazgos(respuesta)
    if hallazgos is None:
        return {
            "hallazgos_logica": [],
            "archivos_revisados": [ruta for ruta, _ in archivos],
            "archivos_omitidos": omitidos,
            "contexto_semgrep_usado": bool(semgrep_findings),
            "generado_en": generado_en,
            "respuesta_cruda_no_parseable": respuesta[:2000],
            "trace": [_trace("revisar_codigo", "respuesta del LLM no era JSON parseable")],
        }

    return {
        "hallazgos_logica": hallazgos,
        "archivos_revisados": [ruta for ruta, _ in archivos],
        "archivos_omitidos": omitidos,
        "contexto_semgrep_usado": bool(semgrep_findings),
        "generado_en": generado_en,
        "trace": [
            _trace(
                "revisar_codigo",
                f"{len(hallazgos)} hallazgo(s) de lógica de negocio sobre {len(archivos)} archivo(s)",
            )
        ],
    }
