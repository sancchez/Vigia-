"""Estado compartido del grafo LangGraph — sección 8.1 del plan.

Define el `PipelineState` que fluye entre todos los nodos del pipeline
(orquestador, recon, escaneo, verificación, priorización, remediación,
reportería, anti-suplantación), más el log de trazabilidad que exige la
sección 8.1 ("cada llamada a herramienta, cada decisión de cada agente, y
cada output se guarda con timestamp y contexto").

Campo crítico: `autorizacion_firmada`. Es un `bool` plano, leído por una
condición de edge determinista en `orchestrator/graph.py` — nunca por una
instrucción de prompt. Ver `gate_autorizacion()` en ese módulo.
"""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Any, Optional, TypedDict

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    """Una entrada del log de trazabilidad (sección 8.1: "trazabilidad completa").

    Es la materia prima del loop de mejora continua (sección 8.2): bitácora
    de fallos, medición de precisión/recall, auditoría de qué pasó en cada
    corrida.
    """

    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    agente: str
    accion: str
    resultado: str


def make_trace_event(agente: str, accion: str, resultado: str) -> dict:
    """Helper que usan todos los nodos para agregar una entrada al trace_log.

    Cada nodo del grafo DEBE llamar esto antes de retornar (regla no
    opcional del encargo — ver sección 8.1 y 8.2 del plan).
    """
    return TraceEvent(agente=agente, accion=accion, resultado=resultado).model_dump()


class Scope(TypedDict, total=False):
    """Dominios/apps/IPs que el cliente autorizó explícitamente a evaluar."""

    dominios: list[str]
    apps: list[str]
    ips: list[str]
    notas: str


class PipelineState(TypedDict, total=False):
    """Estado compartido de todo el grafo (sección 8.1)."""

    # --- Entradas del caso ---
    target: str
    """Dominio/marca principal del cliente (ej. 'miempresa.com')."""

    autorizacion_firmada: bool
    """Puerta de seguridad. Ver `gate_autorizacion()` en graph.py — NUNCA se
    interpreta desde un prompt, solo desde código Python determinista."""

    scope: Scope
    """Activos concretos autorizados para escaneo activo."""

    contexto_negocio: str
    """Qué hace la empresa, qué sistemas son críticos (usado por Priorización)."""

    antisuplantacion_habilitado: bool
    """Activa la rama opcional de Anti-Suplantación (sección 4, módulo Anti-Suplantación)."""

    # --- Hallazgos crudos ---
    recon_findings: list[dict]
    """Salida cruda del Agente de Recon (subdominios, tecnologías, fuente)."""

    scan_findings: list[dict]
    """Salida cruda del Agente de Escaneo (Nuclei/ZAP/Trivy/Grype/Semgrep/OSV)."""

    antisuplantacion_findings: list[dict]
    """Salida cruda + análisis del Agente Anti-Suplantación."""

    # --- Hallazgos procesados ---
    verified_findings: list[dict]
    """Salida del Agente de Verificación determinista: cada item incluye
    `confirmado: bool` y la evidencia (o falta de ella) contra Exploit-DB/OSV."""

    prioritized_findings: list[dict]
    """Hallazgos verificados y confirmados, ordenados por riesgo de negocio."""

    remediations: list[dict]
    """Instrucciones concretas de arreglo por hallazgo priorizado."""

    # --- Salida final ---
    reporte_final: Optional[str]
    """Reporte compilado (markdown) listo para el cliente."""

    autorizacion_bloqueo_motivo: Optional[str]
    """Si no vacío, la fase de Escaneo Activo NUNCA se ejecutó y este campo
    explica por qué (falta autorización firmada) — sección 8.1."""

    # --- Trazabilidad (sección 8.1 y 8.2) ---
    trace_log: Annotated[list[dict], operator.add]
    """Lista de TraceEvent (como dict). Usa `operator.add` como reducer
    porque varios nodos pueden ejecutar en paralelo (ej. Recon y
    Anti-Suplantación) dentro del mismo superstep de LangGraph — sin este
    reducer, dos escrituras concurrentes a la misma clave chocarían."""


def new_state(
    target: str,
    autorizacion_firmada: bool = False,
    scope: Optional[Scope] = None,
    contexto_negocio: str = "",
    antisuplantacion_habilitado: bool = False,
) -> PipelineState:
    """Construye un `PipelineState` inicial válido para arrancar el grafo."""
    return PipelineState(
        target=target,
        autorizacion_firmada=autorizacion_firmada,
        scope=scope or Scope(dominios=[], apps=[], ips=[], notas=""),
        contexto_negocio=contexto_negocio,
        antisuplantacion_habilitado=antisuplantacion_habilitado,
        recon_findings=[],
        scan_findings=[],
        antisuplantacion_findings=[],
        verified_findings=[],
        prioritized_findings=[],
        remediations=[],
        reporte_final=None,
        autorizacion_bloqueo_motivo=None,
        trace_log=[],
    )
