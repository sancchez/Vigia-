"""Servicio FastAPI del MVP (Fase 1, sección 5 del plan maestro).

Envuelve `orchestrator.graph` (ya construido por otro agente) detrás de un
par de endpoints HTTP simples. No reimplementa ninguna regla de negocio: la
capa HTTP solo valida la FORMA de los datos de entrada (Pydantic) y deja que
el grafo decida (nodo determinista `gate_autorizacion`) si el escaneo activo
puede ejecutarse.
"""
