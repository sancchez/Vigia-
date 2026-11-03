# Agente de Priorizacion — v2 (fallback publico / community)

- **Version:** v2
- **Fecha:** 2026-08-01
- **Fuente:** texto que el codigo ya venia usando como fallback open-core en
  `agents/priorizacion.py`, traido a este archivo al centralizar los prompts
  en `agents/prompts.py` (Fase 3).
- **Import vigente:** `agents.priorizacion.SYSTEM_PROMPT`, que resuelve
  `get_prompt("priorizacion")` -> este archivo **solo si `vigia_core_private`
  no esta instalado**. Con el paquete privado presente gana
  `vigia_core_private.priorizacion.SYSTEM_PROMPT` (ver `docs/open-core.md`).

## Prompt

```
Recibes hallazgos de seguridad ya verificados. Ordena los hallazgos de mayor a menor urgencia usando ÚNICAMENTE la severidad técnica reportada (CVSS/severity). Esta es la versión community: no incorpora contexto de negocio del cliente para ponderar impacto real (esa capa es parte del paquete privado de Vigia).
```

## Changelog

- **v2 (2026-08-01)** — version community del prompt: quita la ponderacion por
  contexto de negocio que describe v1 (esa capa es del paquete privado) y deja
  explicito el ordenamiento por severidad tecnica. No es una "mejora" sobre v1
  sino la variante que el repo publico ejecuta de verdad; v1 queda en git como
  el texto original del plan maestro (y como referencia de lo que hace la capa
  privada).
- **v1 (2026-07-14)** — version inicial extraida del plan.
