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

## Anti-suplantación de marca (el módulo más maduro)

Qué hace, medido — no lo que sería lindo que hiciera:

| Capacidad | Estado real |
|---|---|
| Detectar dominios clon (typosquatting) | Sí. `dnstwist` sobre el dominio verificado del cliente, solo dominios REGISTRADOS. Corrida real medida: 45 lookalikes registrados sobre una marca bancaria. |
| **Descartar los registros defensivos del propio cliente** | Sí, y es el diferenciador real: 10 de esos 45 eran del propio banco (comparten nameserver autoritativo). Reportárselos como "amenazas" sería un falso positivo comercial grave. Quedan 35 amenazas de terceros. |
| Marcar los urgentes | Sí. 7 de los 35 tienen registro MX activo → pueden ENVIAR correo que aparente venir del cliente. Se elevan a `critical`. |
| Evidencia por dominio | Scoring de infraestructura 0-4 (DNS-A/MX/SSL/HTTP), screenshot best-effort, y datos RDAP reales (registrador + contacto de abuso). |
| Borrador de takedown | Sí, **determinista** (`tools/takedown.py`): no depende de que haya API key de LLM, y trae el destinatario de abuso real de ESE dominio, no un texto genérico. |
| Ciclo de vida del hallazgo | Sí: detectado → triage → takedown enviado (con evidencia de qué se envió y a quién) → sin respuesta del registrador (auditado, repetible) → resuelto/desestimado. Alimenta el KPI TTTD. |
| Perfiles falsos en redes | Parcial y declarado como tal. Sherlock confirma que existe un perfil con ese nombre, **no** que suplante — se persiste como `info` para revisión humana, sin score de infraestructura (no aplica). |
| Vigilancia continua | Dos niveles, ver abajo. |
| Latencia del botón "Proteger marca" | **Minutos, no segundos** — medido: >15 min sobre el dominio con 45 lookalikes. El costo no es la búsqueda (rápida) sino verificar infraestructura y capturar evidencia de cada dominio sospechoso, con pausas deliberadas entre consultas a terceros. Por eso `POST /antisuplantacion` es **asíncrono**: responde 202 con un `scan_id` y el avance se consulta en `GET /antisuplantacion/runs/{scan_id}`; el frontend hace polling y muestra el progreso real. |

### Vigilancia continua: los dos niveles, sin ambigüedad

* **Barrido programado (default, sin infraestructura extra).**
  `api/scheduler.py::run_brand_cycle_once()` re-corre la detección sobre cada
  dominio verificado y cada marca vigilada cada `VIGIA_BRAND_CYCLE_HOURS`
  (default 24h). Latencia de detección: **horas**.
* **Certificate Transparency en vivo (opt-in).**
  `api/certstream_listener.py` + `deploy/certstream/docker-compose.yml`
  (feed self-hosted `certstream-server-go`, **verificado funcionando el
  2026-08-01**). Detecta el clon cuando le emiten el certificado SSL, a menudo
  antes de que el sitio de phishing esté armado. Latencia: **minutos**.

El feed público histórico de CertStream (`certstream.calidog.io`) está
descontinuado: acepta el handshake y no transmite nada. Por eso el default
apunta al feed local — si no lo levantaste, falla ruidosamente en vez de
fingir que vigila.

### Límite legal (aplica también a marca)

Una marca vigilada debe estar anclada a un dominio del tenant **ya verificado**
(DNS TXT o archivo `.well-known`). No se puede pedir vigilancia sobre la marca
de un tercero: sin esa verificación, `POST /antisuplantacion` responde 403 y
la marca no entra en ninguna de las dos vías de vigilancia continua.

## Stack

Python 3.12 · LangGraph · FastAPI · Claude (Anthropic) · SQLite/Supabase.

## Pricing / Go-to-Market

Ver `PRICING.md` — estrategia de venta, planes y guion de outreach (separado de la documentación técnica).
