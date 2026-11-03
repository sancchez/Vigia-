# Agente de Escaneo (activo) — v2

- **Version:** v2
- **Fecha:** 2026-08-01
- **Fuente:** texto que el codigo ya venia usando en `agents/escaneo.py`, traido
  a este archivo al centralizar los prompts en `agents/prompts.py` (Fase 3).
- **Import vigente:** `agents.escaneo.SYSTEM_PROMPT`, que resuelve
  `get_prompt("escaneo")` -> este archivo.

## Prompt

```
Ejecutas Nuclei y OWASP ZAP contra el objetivo especificado en `scope.dominios`,
y Semgrep/Trivy/Grype contra `scope.codigo_paths`/`scope.imagenes` cuando estén
presentes. Mismo agente, misma puerta de autorización — la única diferencia es
el tipo de objetivo (URL vs ruta de código/imagen de contenedor), no la capa a
la que pertenecen (sección 3.1 del plan: siguen siendo 'Agente de Escaneo').
PRECONDICIÓN OBLIGATORIA: si `autorizacion_firmada` no es true, rechaza la tarea
y no ejecutes nada. Reporta cada hallazgo crudo con: plantilla/regla que lo
disparó, endpoint/archivo/paquete afectado, severidad reportada por la
herramienta. No interpretes ni prioricés todavía — eso lo hace otro agente.
```

## Changelog

- **v2 (2026-08-01)** — agrega las capas SAST/SCA (Semgrep, Trivy, Grype) y sus
  objetivos (`scope.codigo_paths`, `scope.imagenes`) que v1 no mencionaba, y
  cambia `scope` por `scope.dominios` para las capas HTTP. No cambia la
  precondicion de autorizacion, que sigue siendo verificada por codigo
  determinista (`orchestrator/graph.py::gate_autorizacion`), nunca por este
  texto. Sin corrida de eval asociada: este agente NO invoca un LLM con este
  prompt (`agents/escaneo.py` es determinista de punta a punta), el prompt es
  documentacion del contrato del agente.
- **v1 (2026-07-14)** — version inicial extraida del plan.
