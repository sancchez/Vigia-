# Vigia

Plataforma de agentes de IA (LangGraph + FastAPI + Claude) para **gestión de superficie de ataque (attack surface management)** y **anti-suplantación de marca**, dirigida a pymes colombianas. No reinventa escáneres de vulnerabilidades: orquesta herramientas ya existentes, gratuitas y probadas por la industria (Nuclei, OWASP ZAP, Amass, Subfinder, dnstwist, Sherlock, entre otras) y pone encima una capa de inteligencia que esas herramientas no tienen — verificación determinista de hallazgos, priorización según el riesgo real de negocio, redacción de remediación entendible, y reportes que un dueño de pyme sin conocimiento técnico puede leer. El modelo de negocio es de suscripción (no proyecto único), inspirado en jugadores como Detectify, Intruder.io o BrandShield, adaptado a ticket y realidad de pyme colombiana.

## Regla de oro (no negociable)

**Nunca se escanea ni se prueba nada que el dueño del sistema no haya autorizado por escrito.** Antes de tocar cualquier activo de un cliente real debe existir una autorización de pruebas de seguridad firmada (ver `legal/autorizacion-pruebas-seguridad.md`). Sin ese documento firmado, no se ejecuta ningún escaneo activo — solo se prueba contra aplicaciones de laboratorio hechas para practicar (OWASP Juice Shop, DVWA, HackTheBox, TryHackMe). El acceso no autorizado a un sistema informático es delito en Colombia bajo la Ley 1273 de 2009, sin importar la intención de quien lo hizo. Ver `plan-proyecto-ciberseguridad.md`, sección 0, para el detalle completo.

## Estructura de carpetas

```
ciberseguridad/
  legal/            Plantillas legales — autorización de pruebas de seguridad, cumplimiento normativo.
  orchestrator/      Agente Orquestador (LangGraph) — decide el flujo, guarda estado, aplica la puerta de autorización.
  agents/           Subagentes especializados: Recon, Escaneo, Verificación, Priorización, Remediación, Reportería, Anti-Suplantación.
  tools/            Wrappers de herramientas externas (Nuclei, ZAP, Amass, dnstwist, etc.) expuestos como tools de LangChain/MCP.
  eval/             Set de evaluación y métricas (precisión/recall) contra apps de laboratorio con vulnerabilidades conocidas.
  docs/             Documentación técnica y de producto.
  scripts/          Scripts auxiliares (setup, mantenimiento, utilidades de desarrollo).
```

## Estado actual

**Fase 0 (base legal)** y **Fase 1 (MVP)** completas: pipeline LangGraph de punta a punta (`orchestrator/` + `agents/`), wrappers de herramientas (`tools/`), harness de evaluación contra OWASP Juice Shop (`eval/`), servicio FastAPI (`api/`), y una validación real (anonimizada) del módulo anti-suplantación contra un negocio pyme colombiano. Ver `plan-proyecto-ciberseguridad.md` para el plan maestro completo (modelo de negocio, arquitectura de agentes, inventario de herramientas, fases 0-5, y estrategia open-core). Solo se prueba contra aplicaciones de laboratorio hasta tener el primer cliente real con autorización firmada.

## Stack

Python 3.12 · LangGraph · FastAPI · Claude (Anthropic) · SQLite/Supabase.
