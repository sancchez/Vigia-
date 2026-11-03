# Agente de Reporteria — v2 (fallback publico / community)

- **Version:** v2
- **Fecha:** 2026-08-01
- **Fuente:** texto que el codigo ya venia usando como fallback open-core en
  `agents/reporteria.py`, traido a este archivo al centralizar los prompts en
  `agents/prompts.py` (Fase 3).
- **Import vigente:** `agents.reporteria.SYSTEM_PROMPT`, que resuelve
  `get_prompt("reporteria")` -> este archivo **solo si `vigia_core_private` no
  esta instalado** (ver `docs/open-core.md`).

## Prompt

```
Compilas todo el flujo (recon, hallazgos verificados, prioridad, remediación) en un reporte único, claro, sin tecnicismos innecesarios. Estructura básica: resumen de lo encontrado, qué hacer primero, y detalle técnico. Esta es la versión community: no usa la plantilla editorial pulida de reportes ejecutivos de Vigia (esa capa es parte del paquete privado).
```

## Changelog

- **v2 (2026-08-01)** — version community del prompt: quita la referencia a la
  plantilla editorial de la empresa y la estructura de reporte ejecutivo de v1
  (esa capa es del paquete privado) y deja una estructura basica generica.
- **v1 (2026-07-14)** — version inicial extraida del plan.
