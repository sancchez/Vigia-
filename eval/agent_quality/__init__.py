"""Fase 7 (openspec/changes/fase7-confianza-calidad): evals de CALIDAD de
los agentes de razonamiento (priorización, remediación, revisión de código
IA), a diferencia de `eval/run_eval.py` (que mide precisión/recall de
DETECCIÓN de hallazgos).

Alcance estricto -- ver `docs/open-core.md` y `spec.md` de este change:
todo lo de acá corre ÚNICAMENTE contra el modo `fallback_publico` de esos
agentes (el `SYSTEM_PROMPT` genérico que usa este repo cuando
`vigia_core_private` no está instalado). Nunca se asertan los prompts
reales ni el contenido de `vigia_core_private` -- ese plan vive
documentado (no ejecutado) en `tasks.md`, Fase 6, para un commit separado
en ese repo privado.
"""
