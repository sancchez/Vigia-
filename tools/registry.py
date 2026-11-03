"""Registro de herramientas PASIVAS expuestas como `@tool` de LangChain — Fase 3.

Qué es esto y qué NO es
-----------------------

Esto es un envoltorio delgado sobre funciones que YA EXISTEN en
`tools/recon.py` y `tools/exploit_intel.py`. No cambia ni una línea de esas
funciones: solo les pone el decorador `@tool`, un docstring pensado para que
un LLM entienda cuándo llamarlas, y una normalización de la salida a tipos
JSON-serializables (los `@dataclass` de esos módulos no lo son).

El objetivo de esta fase es que estas tools EXISTAN y estén probadas, listas
para que un futuro nodo con `bind_tools(...)` las use. Ningún nodo del grafo
las invoca todavía — el pipeline sigue llamando a las funciones originales de
forma determinista, exactamente como antes.

REGLA DE SEGURIDAD NO NEGOCIABLE
--------------------------------

**Aquí solo entran herramientas PASIVAS**: consultan fuentes públicas (APIs
OSINT de terceros, certificados públicos) o archivos locales (el índice CSV de
Exploit-DB). Ninguna envía tráfico de ataque, ni tráfico no solicitado, contra
infraestructura del cliente o de terceros.

Lo que NUNCA puede entrar a este registro, sin excepción ni caso especial:

- `tools/scan.py` — Nuclei, OWASP ZAP, Semgrep, Trivy, Grype.
- `tools/zap_api.py` — cualquier función de la API de ZAP (spider, active scan).
- `tools/deteccion_dirigida.py` — chequeos dirigidos (SQLi, IDOR, JWT, path
  traversal): envían tráfico de ataque específico contra el objetivo.

Esas herramientas ejecutan escaneo activo, y bajo la Ley 1273 de Colombia su
ejecución sin autorización firmada es un delito. Su única puerta de entrada
legítima es el flujo determinista del grafo, detrás de
`orchestrator/graph.py::gate_autorizacion` — un `if` de Python que NINGÚN LLM
puede razonar, reinterpretar ni saltarse. Exponerlas como `@tool` invocable
por un modelo movería esa decisión a un prompt, que es exactamente lo que la
arquitectura del proyecto existe para impedir.

Criterio ante la duda: **si no estás seguro de si una herramienta es pasiva o
activa, es activa.** No la agregues.

`tests/test_registry_tools_pasivas.py` verifica ambas cosas: que cada tool del
registro tenga un schema válido, y que ningún nombre de herramienta de escaneo
activo aparezca acá.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from langchain_core.tools import tool

from .exploit_intel import find_exploits_for_cve, search_exploitdb
from .recon import run_amass_passive, run_subfinder

# --- Guardas de superficie ---------------------------------------------------
# Nombres de módulo cuya presencia en este archivo sería un fallo de seguridad,
# no un detalle de estilo. Se listan como datos (no como comentario) para que el
# test de regresión pueda afirmarlo sobre el código fuente real de este módulo.
MODULOS_ESCANEO_ACTIVO_PROHIBIDOS = ("tools.scan", "tools.zap_api", "tools.deteccion_dirigida")

HERRAMIENTAS_ACTIVAS_PROHIBIDAS = (
    "nuclei",
    "zap",
    "semgrep",
    "trivy",
    "grype",
)


def _recon_a_dict(resultado: Any) -> dict:
    """Normaliza un `ReconResult` a algo que un LLM pueda leer.

    Se descarta `raw` (el `ToolResult` con stdout/stderr completos) a propósito:
    puede ser de megabytes y no aporta nada al razonamiento, solo quema
    contexto. El detalle crudo sigue disponible llamando la función original.
    """
    return {
        "dominio": resultado.domain,
        "subdominios": list(resultado.subdomains),
        "total": len(resultado.subdomains),
    }


def _exploits_a_dicts(registros: list) -> list[dict]:
    return [asdict(r) for r in registros]


@tool("recon_subdominios_subfinder", parse_docstring=True)
def recon_subdominios_subfinder(dominio: str) -> dict:
    """Enumera subdominios públicos de un dominio con Subfinder (100% pasivo).

    Consulta únicamente fuentes públicas de terceros (certificados,
    agregadores OSINT). NO envía tráfico al dominio objetivo, así que es
    seguro de usar antes de que exista autorización firmada de escaneo.

    Args:
        dominio: dominio raíz a enumerar, ej. "miempresa.com" (sin esquema
            http/https, sin ruta).

    Returns:
        dict con `dominio`, `subdominios` (lista de strings) y `total`.
        Si el binario `subfinder` no está instalado, devuelve un dict con
        la clave `error` explicando cómo instalarlo, en vez de fallar.
    """
    try:
        return _recon_a_dict(run_subfinder(dominio))
    except Exception as exc:
        return {"dominio": dominio, "subdominios": [], "total": 0, "error": str(exc)}


@tool("recon_subdominios_amass", parse_docstring=True)
def recon_subdominios_amass(dominio: str) -> dict:
    """Enumera subdominios con OWASP Amass en modo pasivo (`enum -passive`).

    Complementa a `recon_subdominios_subfinder` con otro conjunto de fuentes
    OSINT. Modo pasivo estricto: sin resolución activa ni fuerza bruta de DNS
    contra el objetivo. Es más lenta que Subfinder; llamarla cuando hace falta
    una segunda fuente, no por default.

    Args:
        dominio: dominio raíz a enumerar, ej. "miempresa.com".

    Returns:
        dict con `dominio`, `subdominios` y `total` (o `error` si Amass no
        está instalado).
    """
    try:
        return _recon_a_dict(run_amass_passive(dominio))
    except Exception as exc:
        return {"dominio": dominio, "subdominios": [], "total": 0, "error": str(exc)}


@tool("exploitdb_buscar", parse_docstring=True)
def exploitdb_buscar(consulta: str, limite: int = 10) -> list[dict]:
    """Busca exploits públicos por texto libre en el índice local de Exploit-DB.

    Consulta un CSV en disco (el mismo índice que usa `searchsploit`), sin
    red y sin tocar ningún sistema. Útil para saber si un producto/versión
    detectado en recon tiene prueba de concepto pública documentada.

    Args:
        consulta: texto a buscar en la descripción del exploit, ej.
            "Apache Struts 2 remote code execution".
        limite: máximo de resultados (por default 10; subirlo quema contexto).

    Returns:
        Lista de dicts con `edb_id`, `description`, `date_published`,
        `platform`, `type` y `cves`. Lista vacía si no hay coincidencias.
    """
    try:
        return _exploits_a_dicts(search_exploitdb(consulta, limit=limite))
    except Exception as exc:
        return [{"error": str(exc)}]


@tool("exploitdb_por_cve", parse_docstring=True)
def exploitdb_por_cve(cve_id: str, limite: int = 10) -> list[dict]:
    """¿Este CVE concreto tiene prueba de concepto pública en Exploit-DB?

    Es la consulta de verificación determinista del pipeline: un CVE con
    exploit público documentado es materialmente más urgente que uno sin él.
    Offline, sin red.

    Args:
        cve_id: identificador exacto, ej. "CVE-2021-44228".
        limite: máximo de resultados.

    Returns:
        Lista de dicts (mismo esquema que `exploitdb_buscar`). Lista vacía
        significa "no hay PoC pública indexada para ese CVE", que es un
        resultado válido y NO un error.
    """
    try:
        return _exploits_a_dicts(find_exploits_for_cve(cve_id, limit=limite))
    except Exception as exc:
        return [{"error": str(exc)}]


# Orden estable: es lo que consumiría un futuro `llm.bind_tools(TOOLS_PASIVAS)`.
TOOLS_PASIVAS = [
    recon_subdominios_subfinder,
    recon_subdominios_amass,
    exploitdb_buscar,
    exploitdb_por_cve,
]


def get_tools_pasivas() -> list:
    """Copia de la lista de tools pasivas (para no exponer la lista mutable)."""
    return list(TOOLS_PASIVAS)
