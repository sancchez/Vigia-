"""Servicio FastAPI del MVP — Fase 1 (sección 5 del plan maestro).

    "Envolver Nuclei + ZAP baseline en un servicio FastAPI simple. Un solo
    agente dispara el escaneo, Claude arma un reporte básico. Se prueba
    únicamente contra apps de laboratorio."

Esta capa HTTP es deliberadamente delgada: NO decide si el escaneo activo
puede ejecutarse. Esa decisión es 100% del nodo determinista
`gate_autorizacion` en `orchestrator/graph.py` (sección 8.1 del plan — "la
seguridad del sistema no depende de que el LLM se acuerde de la regla", y
tampoco depende de que esta capa HTTP se acuerde). Aquí solo se valida la
FORMA de los datos de entrada (Pydantic) y se invoca el grafo ya compilado.

AVISO LEGAL — léelo antes de apuntar este servicio a cualquier lado:
Este servicio NUNCA debe usarse contra dominios/IPs/apps de terceros sin
una autorización de pruebas de seguridad firmada por el dueño del sistema
(ver `legal/autorizacion-pruebas-seguridad.md` y sección 0 del plan
maestro). El acceso no autorizado a un sistema informático es delito en
Colombia bajo la Ley 1273 de 2009, sin importar la intención. Mientras no
exista ese documento firmado para un cliente real, las únicas pruebas
válidas son contra aplicaciones de laboratorio de código abierto pensadas
para practicar: OWASP Juice Shop (ver `eval/setup_juiceshop.md`) y DVWA.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel, Field

from orchestrator.graph import compile_graph
from orchestrator.state import new_state

load_dotenv()

AVISO_LEGAL = (
    "Este servicio NO debe apuntarse contra sistemas de terceros sin una "
    "autorizacion de pruebas de seguridad firmada por el dueno (ver "
    "legal/autorizacion-pruebas-seguridad.md). Las unicas pruebas de "
    "referencia habilitadas hoy son aplicaciones de laboratorio: OWASP "
    "Juice Shop (eval/setup_juiceshop.md) y DVWA. Si 'autorizacion_firmada' "
    "no es true, el grafo bloquea el Escaneo Activo de forma determinista "
    "(ver orchestrator/graph.py::gate_autorizacion) — esta capa HTTP no "
    "aplica ni duplica esa regla, solo la reporta."
)

_compiled_graph: Any = None


def _get_graph() -> Any:
    """Compila el grafo una sola vez (module-level cache) y lo reutiliza.

    Compilar por request sería correcto pero desperdicia trabajo — el grafo
    no tiene estado propio entre invocaciones (`PipelineState` viaja como
    argumento de `.invoke()`), así que un solo `CompiledStateGraph` es
    seguro de reutilizar entre requests concurrentes.
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = compile_graph()
    return _compiled_graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Compila el grafo al arrancar (falla rápido si algo del ensamblaje del
    # StateGraph está roto, en vez de fallar en el primer /scan).
    _get_graph()
    yield


app = FastAPI(
    title="Ciberseguridad IA — API MVP (Fase 1)",
    description=(
        "Servicio FastAPI que envuelve el pipeline LangGraph de escaneo de "
        "seguridad (Recon -> gate autorizacion -> Escaneo -> Verificacion -> "
        "Priorizacion -> Remediacion -> Reporteria). "
        + AVISO_LEGAL
    ),
    version="0.1.0",
    lifespan=lifespan,
)


class ScopeIn(BaseModel):
    """Espejo de `orchestrator.state.Scope` — solo forma, sin reglas de negocio."""

    dominios: list[str] = Field(default_factory=list)
    apps: list[str] = Field(default_factory=list)
    ips: list[str] = Field(default_factory=list)
    notas: str = ""


class ScanRequest(BaseModel):
    """Payload de `POST /scan`.

    IMPORTANTE: `autorizacion_firmada=False` es un valor VÁLIDO y aceptado
    por este endpoint — la petición no se rechaza en la capa HTTP. Es el
    grafo (nodo `gate_autorizacion`, código determinista) el que decide
    bloquear el Escaneo Activo y desviar el flujo a `bloqueo_autorizacion`.
    Duplicar esa validación aquí violaría la sección 8.1 del plan maestro
    (una sola fuente de verdad para la puerta de seguridad).
    """

    target: str = Field(..., description="Dominio/marca principal, ej. 'miempresa.com'")
    scope: ScopeIn = Field(default_factory=ScopeIn)
    autorizacion_firmada: bool = Field(
        default=False,
        description=(
            "Debe ser true y estar respaldado por un documento firmado "
            "(legal/autorizacion-pruebas-seguridad.md) para que el grafo "
            "permita Escaneo Activo. false es aceptado igual: el grafo "
            "responde con un reporte explicando el bloqueo."
        ),
    )
    contexto_negocio: str = Field(
        default="", description="Qué hace la empresa / qué sistemas son críticos (usa Priorización)"
    )
    antisuplantacion_habilitado: bool = Field(
        default=False, description="Activa la rama opcional Anti-Suplantación (sección 4 del plan)"
    )


class ScanResponse(BaseModel):
    """Proyección del `PipelineState` final tras `.invoke()` — sin reglas propias."""

    target: str
    autorizacion_firmada: bool
    autorizacion_bloqueo_motivo: Optional[str] = None
    recon_findings: list[dict] = Field(default_factory=list)
    scan_findings: list[dict] = Field(default_factory=list)
    verified_findings: list[dict] = Field(default_factory=list)
    prioritized_findings: list[dict] = Field(default_factory=list)
    remediations: list[dict] = Field(default_factory=list)
    antisuplantacion_findings: list[dict] = Field(default_factory=list)
    reporte_final: Optional[str] = None
    trace_log: list[dict] = Field(default_factory=list)
    aviso_legal: str = AVISO_LEGAL


@app.get("/health")
def health() -> dict:
    """Healthcheck simple. No toca el grafo ni ninguna herramienta externa."""
    return {
        "status": "ok",
        "servicio": "ciberseguridad-ia-api",
        "fase": "Fase 1 (MVP) — sección 5 del plan maestro",
        "anthropic_api_key_configurada": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "aviso_legal": AVISO_LEGAL,
    }


@app.post("/scan", response_model=ScanResponse)
def scan(payload: ScanRequest) -> ScanResponse:
    """Dispara el pipeline completo (`orchestrator.graph`) para `target`.

    Esta función NO decide nada sobre autorización: arma el `PipelineState`
    inicial exactamente con lo que llegó en el request (forma validada por
    Pydantic) y deja que el grafo compilado (`_get_graph().invoke(...)`)
    tome todas las decisiones de flujo, incluyendo el bloqueo determinista
    de Escaneo Activo cuando `autorizacion_firmada` no es `true`.
    """
    estado_inicial = new_state(
        target=payload.target,
        autorizacion_firmada=payload.autorizacion_firmada,
        scope=payload.scope.model_dump(),
        contexto_negocio=payload.contexto_negocio,
        antisuplantacion_habilitado=payload.antisuplantacion_habilitado,
    )

    grafo = _get_graph()
    estado_final = grafo.invoke(estado_inicial)

    return ScanResponse(
        target=estado_final.get("target", payload.target),
        autorizacion_firmada=estado_final.get("autorizacion_firmada", payload.autorizacion_firmada),
        autorizacion_bloqueo_motivo=estado_final.get("autorizacion_bloqueo_motivo"),
        recon_findings=estado_final.get("recon_findings") or [],
        scan_findings=estado_final.get("scan_findings") or [],
        verified_findings=estado_final.get("verified_findings") or [],
        prioritized_findings=estado_final.get("prioritized_findings") or [],
        remediations=estado_final.get("remediations") or [],
        antisuplantacion_findings=estado_final.get("antisuplantacion_findings") or [],
        reporte_final=estado_final.get("reporte_final"),
        trace_log=estado_final.get("trace_log") or [],
    )
