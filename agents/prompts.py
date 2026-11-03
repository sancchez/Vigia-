"""Fuente única de verdad de los system prompts — Fase 3 (agentización).

Problema que resuelve: hasta esta fase, los 8 archivos de
`eval/prompt_versions/` tenían el prompt "oficial" versionado en git (punto 3
de la sección 8.2 del plan: *"un cambio de prompt es un commit, no una edición
perdida en un chat"*) pero NINGÚN `.py` los leía. Cada `agents/*.py` llevaba
su propio `SYSTEM_PROMPT` como literal Python, y los dos se desincronizaron
sin que nada lo notara. Este módulo hace que el `.md` versionado sea el único
lugar donde vive el texto.

Formato de parseo (deliberadamente simple, ver `eval/prompt_versions/README.md`
sección "Estructura de cada archivo de version"):

    ## Prompt

    ```
    <texto exacto del system prompt>
    ```

    ## Changelog

Es decir: se toma el PRIMER bloque de código con fences (```) que aparece
después del encabezado `## Prompt` y antes del siguiente `##`. Nada más se
interpreta — ni el encabezado, ni el changelog, ni el resto del archivo.

Versión vigente: NO se elige "la más alta que exista". El README de
`eval/prompt_versions/` es explícito en que un `v{N+1}` puede quedar en git
como experimento documentado mientras producción sigue apuntando a la versión
anterior hasta que las métricas mejoren. Por eso la versión vigente de cada
agente es un pin explícito en `_VERSION_VIGENTE` — cambiar de versión es
editar esa tabla, un commit deliberado, no un efecto secundario de crear un
archivo nuevo.

Open-core: este módulo NO reemplaza el mecanismo de override de
`vigia_core_private` (ver `docs/open-core.md`). Los agentes que tienen capa
privada (`priorizacion`, `remediacion`, `reporteria`) siguen intentando
primero `from vigia_core_private.X import SYSTEM_PROMPT` y solo caen a
`get_prompt(...)` si el paquete privado no está instalado. Lo que este módulo
aporta ahí es que el prompt del FALLBACK público también esté versionado en un
`.md` en vez de ser un literal suelto — por eso los `v2_*` de esos tres
agentes documentan explícitamente que son la versión community.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent.parent / "eval" / "prompt_versions"

# Versión vigente EN PRODUCCIÓN por agente (ver docstring: pin explícito, no
# "la más alta"). Alinear con la tabla "Registro rapido de versiones" del
# README de `eval/prompt_versions/`.
_VERSION_VIGENTE: dict[str, int] = {
    "orquestador": 1,
    "recon": 1,
    "verificacion": 1,
    "anti_suplantacion": 1,
    # v2: el prompt en código creció para cubrir Semgrep/Trivy/Grype (SAST/SCA),
    # que v1 no mencionaba.
    "escaneo": 2,
    # v2 de estos tres = fallback público open-core (el prompt real vive en
    # `vigia_core_private`, ver docs/open-core.md). v1 sigue en git como el
    # texto original del plan maestro.
    "priorizacion": 2,
    "remediacion": 2,
    "reporteria": 2,
}

# Los módulos de `agents/` no usan exactamente el mismo slug que los archivos
# de `eval/prompt_versions/` (el archivo usa `anti_suplantacion`, el módulo se
# llama `antisuplantacion`). Se aceptan ambos nombres.
_ALIAS: dict[str, str] = {
    "antisuplantacion": "anti_suplantacion",
    "anti-suplantacion": "anti_suplantacion",
}

_BLOQUE_PROMPT = re.compile(
    r"^##\s+Prompt\s*$"      # encabezado de sección
    r"(?P<cuerpo>.*?)"        # todo hasta...
    r"(?=^##\s|\Z)",          # ...el siguiente '## ' o el fin del archivo
    re.DOTALL | re.MULTILINE,
)
_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\n(?P<texto>.*?)```", re.DOTALL)


class PromptNoEncontradoError(RuntimeError):
    """El `.md` de ese agente/versión no existe o no tiene un bloque parseable."""


def _normalizar(nombre_agente: str) -> str:
    slug = nombre_agente.strip().lower().replace(" ", "_")
    return _ALIAS.get(slug, slug)


def ruta_prompt(nombre_agente: str, version: int | None = None) -> Path:
    """Ruta al `.md` versionado de ese agente (no verifica que exista)."""
    slug = _normalizar(nombre_agente)
    if version is None:
        version = _VERSION_VIGENTE.get(slug, 1)
    return PROMPT_DIR / f"v{version}_{slug}.md"


def parsear_prompt(texto_md: str) -> str:
    """Extrae el system prompt de un `.md` de `eval/prompt_versions/`.

    Función pura (recibe texto, no ruta) para que un test pueda ejercitarla
    con casos sintéticos sin tocar el filesystem.
    """
    seccion = _BLOQUE_PROMPT.search(texto_md)
    if seccion is None:
        raise PromptNoEncontradoError(
            "el archivo no tiene una sección '## Prompt' (ver "
            "eval/prompt_versions/README.md, 'Estructura de cada archivo de version')"
        )
    fence = _FENCE.search(seccion.group("cuerpo"))
    if fence is None:
        raise PromptNoEncontradoError(
            "la sección '## Prompt' no contiene un bloque de código con fences (```)"
        )
    contenido = fence.group("texto").strip()
    if not contenido:
        raise PromptNoEncontradoError("el bloque de prompt está vacío")
    return contenido


@lru_cache(maxsize=None)
def get_prompt(nombre_agente: str, version: int | None = None) -> str:
    """System prompt vigente de un agente, leído del `.md` versionado.

    `nombre_agente` acepta tanto el slug del archivo (`anti_suplantacion`)
    como el del módulo de `agents/` (`antisuplantacion`).
    """
    ruta = ruta_prompt(nombre_agente, version)
    if not ruta.is_file():
        raise PromptNoEncontradoError(f"no existe {ruta}")
    return parsear_prompt(ruta.read_text(encoding="utf-8"))


def agentes_disponibles() -> list[str]:
    """Slugs con una versión vigente declarada (orden estable, para tests)."""
    return sorted(_VERSION_VIGENTE)
