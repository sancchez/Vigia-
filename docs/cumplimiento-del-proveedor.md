# Cumplimiento del Proveedor — Dossier de Diligencia Debida de Vigia

> **Documento presentable a cliente y a auditoría.** Estado verificado contra el
> código del repositorio en la fecha de la última revisión (2026-07-26). Cada
> casilla cita una ruta real `archivo:línea`. Ninguna casilla se marca como
> cumplida sin evidencia verificable en el repositorio.
>
> **Revisión 2026-07-26 (segunda pasada).** Las doce casillas se re-evaluaron
> leyendo el código actual, no la revisión anterior. Un lote de controles
> técnicos aterrizó entre ambas pasadas (rate limiting, throttling e identidad
> del bot, tope de escaneos simultáneos, purga automática de capturas,
> verificación de dominio para marcas vigiladas) y las referencias
> `archivo:línea` de todo el documento se recalcularon en consecuencia: las de
> `api/main.py` estaban desplazadas ~110 líneas y habrían apuntado a código
> equivocado. Ninguna casilla cambió de estado; el detalle de por qué está en la
> nota que sigue a la Sección 2.

---

## 0. Alcance y naturaleza de este documento

### 0.1 Qué es este documento (y qué no es)

Este dossier responde una sola pregunta: **¿la operación del servicio Vigia es
lícita y diligente?** Es decir, describe el cumplimiento **del proveedor**.

No debe confundirse con [`docs/cumplimiento.md`](./cumplimiento.md), que es su
**inverso**: aquel documenta el módulo de producto (`agents/cumplimiento.py`,
`GET /reports/cumplimiento`) que ayuda **al cliente** a demostrar *su* propio
cumplimiento mapeando los hallazgos detectados a controles ISO/IEC 27001 Anexo A
y a las obligaciones de la Ley 2573 de 2026.

| Documento | Sujeto del cumplimiento | Pregunta que responde |
|---|---|---|
| [`docs/cumplimiento.md`](./cumplimiento.md) | El **cliente** | ¿Los hallazgos que Vigia le entrega le sirven como evidencia frente a su regulador? |
| **Este documento** | **Vigia (el proveedor)** | ¿Vigia opera de forma lícita y diligente, y puede demostrarlo si un cliente regulado lo audita? |

### 0.2 La distinción clave: sujeto obligado vs. proveedor

**Vigia NO es sujeto obligado directo de la Ley 2573 de 2026.**

La Ley 2573 de 2026 impone obligaciones a un conjunto acotado de actores:
operadores de telecomunicaciones, entidades financieras y crediticias, y
comercios que otorgan o intermedian crédito. Esa delimitación de alcance ya está
codificada en el producto y viaja en **todo** reporte generado
(`LEY2573_ADVERTENCIA_ALCANCE` en `agents/cumplimiento.py`; ver
[`docs/cumplimiento.md`](./cumplimiento.md), sección "El mapeo").

Vigia no es ninguna de esas cosas. Vigia es **proveedor / tercero** de esos
sujetos obligados. Esto tiene tres consecuencias prácticas:

1. **Vigia no hereda las obligaciones legales del cliente.** Vigia no puede
   congelar cobros, ni marcar a una persona como "Víctima de Falsedad Personal"
   ante centrales de riesgo, ni atender los reportes de suplantación que la ley
   le exige atender al sujeto obligado. Ofrecer el servicio como si sustituyera
   esas obligaciones sería una tergiversación con consecuencias legales para
   ambas partes. Ver
   [`legal/clausulas-contractuales.md`](../legal/clausulas-contractuales.md) §3.6.
2. **Vigia sí queda dentro del perímetro de auditoría del cliente.** Cuando un
   banco o una telco contrata a Vigia, el regulador puede exigirle a *ese
   cliente* que demuestre que sus proveedores de seguridad operan con
   diligencia. Este dossier existe para que el cliente pueda responder esa
   pregunta sin trabajo adicional.
3. **La responsabilidad de Vigia es de medio, no de resultado.** Vigia se obliga
   a ejecutar las pruebas con diligencia profesional, no a garantizar la
   detección del 100% de las vulnerabilidades existentes. Ver
   [`legal/clausulas-contractuales.md`](../legal/clausulas-contractuales.md) §3.2.

### 0.3 Régimen que sí aplica directamente a Vigia

Con independencia de la Ley 2573, a Vigia le aplican de forma directa:

- **Ley 1581 de 2012 y Decreto 1074 de 2015** (protección de datos personales /
  habeas data), en calidad de **Encargado del Tratamiento** de los datos
  personales del cliente que atraviesen el escaneo.
- **Ley 1273 de 2009** (delitos informáticos). El escaneo activo sin
  autorización escrita del titular sería un delito. Toda la arquitectura del
  gate de autorización existe por esta razón. Ver
  [`legal/autorizacion-pruebas-seguridad.md`](../legal/autorizacion-pruebas-seguridad.md),
  Sección 9.
- **ISO/IEC 27001:2022 Anexo A**, como marco de referencia voluntario. Vigia
  **no está certificado**. Este dossier es una autoevaluación honesta contra el
  Anexo A, no una certificación ni un sustituto de auditoría externa.

### 0.4 Documentos relacionados

- [`legal/autorizacion-pruebas-seguridad.md`](../legal/autorizacion-pruebas-seguridad.md) — plantilla de Rules of Engagement (RoE)
- [`legal/clausulas-contractuales.md`](../legal/clausulas-contractuales.md) — cláusulas del contrato de prestación de servicios
- [`legal/terminos-de-servicio.md`](../legal/terminos-de-servicio.md) — v1.0 borrador, sin revisión de abogado
- [`legal/politica-uso-aceptable.md`](../legal/politica-uso-aceptable.md) — v1.0 borrador, sin revisión de abogado
- [`legal/aviso-de-privacidad.md`](../legal/aviso-de-privacidad.md) — v1.0 borrador, sin revisión de abogado
- [`legal/politica-de-retencion.md`](../legal/politica-de-retencion.md) — v1.0 borrador, sin revisión de abogado
- [`legal/divulgacion-responsable.md`](../legal/divulgacion-responsable.md) — v1.0 borrador, sin revisión de abogado

Todos los documentos de `legal/` existen ya como archivos versionados en el
repositorio, pero **ninguno ha pasado revisión de abogado ni está publicado en
un sitio institucional**. Un documento redactado y versionado es evidencia de
diligencia; no es todavía un instrumento oponible.

### 0.5 Convención de estados

Se usan exactamente tres estados. Un dossier que infla su cumplimiento es un
riesgo legal mayor que uno con casillas en Pendiente.

| Estado | Significado |
|---|---|
| **Implementado** | El control existe, funciona y hay evidencia verificable en el repositorio. Sin condiciones abiertas. |
| **Parcial** | Existe una parte real y verificable del control, pero falta un componente para considerarlo completo. Lo que falta se nombra explícitamente. |
| **Pendiente** | No existe. No hay evidencia en el código ni en la documentación. |

### 0.6 Base técnica verificada (evidencia de soporte, no checklist)

Antes de las tablas conviene dejar constancia de los controles técnicos que sí
están construidos y verificados. **No son casillas de cumplimiento** — son la
base de evidencia sobre la que se apoyan las secciones 1 y 2.

| Control técnico | Evidencia |
|---|---|
| Gate de autorización determinista (código Python plano, sin LLM) antes de cualquier escaneo activo | `orchestrator/graph.py:92` (`gate_autorizacion`) |
| El nodo de bloqueo es *estructuralmente* incapaz de escanear: no importa `tools/scan.py` ni ningún wrapper | `orchestrator/graph.py:53` (`bloqueo_autorizacion_node`) |
| Revalidación defensiva en el propio nodo de escaneo (defensa en profundidad si se invoca fuera del grafo) | `agents/escaneo.py:34-50` |
| Verificación de propiedad de dominio estilo Search Console: DNS TXT en `_vigia-challenge.<dominio>` o `.well-known/vigia-verification.txt` | `tools/asset_verification.py` |
| Rechazo HTTP 403 si el target no corresponde a un activo verificado del tenant | `api/main.py:3094` (`_asset_verificado_para_target`), aplicado en `:2862` (`POST /scan`), `:3309` (`POST /scan/activo`), `:3491` (`POST /antisuplantacion`) |
| Una marca vigilada debe anclarse a un dominio-activo YA VERIFICADO del tenant, o 403; CertStream ignora las marcas sin anclar | `api/main.py:4042` (`_dominio_verificado_para_marca`), aplicado en `:4112` (`POST /watched-brands`); `api/certstream_listener.py:155-156`; columna `watched_brands.dominio_asociado` en `db/schema.sql:255`, migración en `db/connection.py:143`, `:334` |
| Rate limiting por tenant (clave = `tenant_id` del JWT, IP como *fallback*) en los cuatro endpoints caros | `api/main.py:147` (`clave_rate_limit`), límites `:181-185`, aplicado en `:2245`, `:2839`, `:3294`, `:3455` |
| Throttling y tope de concurrencia del sondeo a dominios de TERCEROS (2 s entre hosts, 10 s al mismo host, 2 simultáneos) | `tools/antisuplantacion.py:109` (`turno_de_sondeo`), `:99` (semáforo), config en `:90-93` |
| User-Agent identificable en todo sondeo a terceros, incluido el Chromium de las capturas — bloqueable a propósito | `tools/antisuplantacion.py:62-71`, aplicado en `:536` (HTTP) y `:675` (navegador) |
| Tope de escaneos activos ZAP simultáneos, con error legible al agotarse en vez de sumar carga | `tools/zap_api.py:182` (`turno_de_escaneo_activo`), reservado en `:622`, `:778` |
| Purga automática de capturas de dominios de terceros a los 90 días (tercer job de APScheduler) | `api/scheduler.py:336` (`run_screenshot_purge_once`), job `vigia_purga_screenshots` en `:428` |
| Anti-DNS-rebinding: la exención de laboratorio se decide sobre el **string literal** del host, nunca sobre resolución DNS en vivo | `tools/asset_verification.py:145-155`, documentado en `:60-70` |
| Trazabilidad persistida por corrida (`trace_log_json`, timestamps, estado) | `db/schema.sql:102-114`; evento en `orchestrator/state.py:23-36` (incluye `timestamp` UTC ISO por defecto) |
| Endpoint de auditoría de solo lectura | `api/main.py:3765` (`GET /audit`), `api/main.py:3741` (`GET /scans/{scan_id}/trace`, auditoría por scan) |
| Contraseñas con bcrypt + salt por usuario | `auth/jwt_auth.py:37-42` |
| Borrado del código fuente del cliente en `finally` (se ejecuta aunque la revisión falle) | `api/main.py:2153` y `api/main.py:2308` |
| Doble consentimiento antes de enviar código del cliente a un LLM de terceros: `autorizacion_firmada` **no** basta | `api/main.py:2249`, `:2268-2280` |
| Decisión de privacidad: **no** se almacena WHOIS de dominios de terceros; la atribución de propiedad se hace por nameserver compartido | `tools/antisuplantacion.py:602-618` |
| Plantilla de Rules of Engagement lista para firma | `legal/autorizacion-pruebas-seguridad.md` |
| Timeouts por herramienta y presupuesto de tiempo acotado por escaneo | `tools/_shared.py:59-73`, `:119-142`; `tools/zap_api.py:503-537` |
| Historial de auditoría de cambios de estado de hallazgo, con `changed_by` | `db/schema.sql:175-186` (`finding_transiciones`) |

---

## 1. Ley 2573 de 2026 — checklist del proveedor

Recordatorio de la sección 0.2: Vigia no es sujeto obligado. Los cinco controles
de esta tabla son las buenas prácticas que Vigia debe poder demostrar **como
proveedor de un sujeto obligado**.

| Requisito | Estado real en Vigia | Evidencia |
|---|---|---|
| **1.1 Identificar si el cliente es sujeto obligado** (banco, telco, fintech, comercio con competencia crediticia) y marcarlo en el contrato para ajustar el alcance del servicio. | **Pendiente.** No existe ningún campo estructurado —ni en `tenants`, ni en `assets`, ni en el registro de usuario— que capture si el cliente es sujeto obligado. Lo más cercano es `contexto_negocio`, un texto libre opcional que se pasa como *query param* y solo alimenta al LLM al generar el reporte; no se persiste como atributo del tenant ni condiciona el alcance de nada. El producto **sí** emite la advertencia genérica de alcance de la ley en todo reporte, pero eso informa al cliente; no lo clasifica. | Tabla `tenants` sin campo de clasificación: `db/schema.sql:1-35`. `contexto_negocio` como query param no persistido: `api/main.py` (`GET /reports/cumplimiento`, ver `docs/cumplimiento.md`). Advertencia genérica: `LEY2573_ADVERTENCIA_ALCANCE` en `agents/cumplimiento.py`. |
| **1.2 No ofrecer el servicio como sustituto de las obligaciones legales del cliente** (congelar cobros, marcar "Víctima de Falsedad Personal", atender reportes de suplantación) — cláusula contractual explícita. | **Parcial.** El producto ya se autolimita técnicamente: `agents/cumplimiento.py` incluye en **cada** reporte la advertencia de alcance de la Ley 2573 y la advertencia ISO 27001, y por diseño no inventa numeración de artículos ni afirma aplicabilidad legal garantizada (decisión documentada). Falta el cierre contractual: la cláusula existe **redactada** (`legal/clausulas-contractuales.md` §3.6) pero **ningún contrato la tiene firmada todavía**, y no ha pasado revisión de abogado. | `agents/cumplimiento.py` (`LEY2573_ADVERTENCIA_ALCANCE`, `ISO27001_ADVERTENCIA`); racional en `docs/cumplimiento.md:20-55`. Cláusula redactada: `legal/clausulas-contractuales.md` §3.6. |
| **1.3 Tratamiento de datos personales durante el escaneo** (logs, IPs, credenciales de prueba): minimización y habeas data. | **Parcial.** Las decisiones de minimización se ampliaron y ahora incluyen supresión automática: el código fuente subido por el cliente se borra en un bloque `finally`; enviar ese código a un LLM de terceros exige un **segundo** consentimiento explícito; se decidió deliberadamente **no** almacenar WHOIS de dominios de terceros; y **las capturas de dominios de terceros ahora sí se purgan solas a los 90 días** por un job periódico de APScheduler, plazo configurable. Existe además la política de retención escrita, con plazos por tipo de dato y el estado real de automatización de cada uno. Falta: (a) no hay cifrado en reposo de la base de datos, que contiene hallazgos, targets y rutas de captura; (b) la purga automática cubre **solo** las capturas — hallazgos, trazas de escaneo, reportes DMARC y cuentas terminadas siguen sin job de vencimiento; (c) el aviso de privacidad está redactado pero **no publicado**, y el procedimiento de atención de derechos de habeas data existe en papel sin responsable ni canal reales asignados (los placeholders del documento siguen sin llenar). | Borrado en `finally`: `api/main.py:2153`, `:2308`. Doble consentimiento: `api/main.py:2249`, `:2268-2280`. No-WHOIS por privacidad: `tools/antisuplantacion.py:602-618`. Purga de capturas a 90 días: `api/scheduler.py:336` (`run_screenshot_purge_once`), `:361` (plazo), job en `:428`. Política escrita: `legal/politica-de-retencion.md` §2, §3. Aviso de privacidad redactado sin publicar: `legal/aviso-de-privacidad.md`. |
| **1.4 Trazabilidad de cada prueba: quién, cuándo, qué alcance, con qué autorización.** Bitácora + autorización escrita. | **Parcial.** El **cuándo** y el **resultado** están sólidos: cada corrida inserta una fila en `scans` con `created_at`, `completed_at`, `estado` y `trace_log_json`, y cada nodo del grafo está obligado a emitir un evento con timestamp UTC; existe un endpoint de auditoría de solo lectura. Faltan tres piezas del "quién / con qué autorización": (a) **la tabla `scans` no registra qué usuario disparó el escaneo** — solo `tenant_id`; no hay columna `user_id`, así que la bitácora identifica la organización, no a la persona; (b) `autorizacion_firmada` es un **booleano autodeclarado en el request**, no la constancia de un documento firmado; (c) las columnas `assets.autorizacion_activa` / `autorizacion_doc_url` / `autorizado_por` existen en el esquema y el *scheduler* **sí las lee** para decidir si una corrida recurrente puede ser activa, pero **ningún endpoint las escribe** — no hay forma, hoy, de registrar en el sistema que el cliente firmó el RoE. La plantilla de autorización escrita existe, pero vive fuera del sistema. **Avance parcial desde la revisión anterior:** el módulo anti-suplantación sí ganó una constancia persistida de legitimación — `watched_brands.dominio_asociado` obliga a anclar cada marca vigilada a un dominio-activo ya verificado del tenant, y queda escrito en la fila. Es el primer caso en el producto de "por qué esta vigilancia es legítima" guardado en base de datos y no autodeclarado en el request; el patrón todavía no se extendió a `scans`. | Bitácora: `db/schema.sql:102-114`; evento con timestamp en `orchestrator/state.py:23-36`; `api/main.py:3765` (`GET /audit`), `:3741` (por scan). Ausencia de `user_id` en `scans`: `db/schema.sql:101-114` (verificado de nuevo tras los cambios de esta revisión: la columna sigue sin existir). `autorizacion_firmada` autodeclarada: `api/main.py:2870`, `:3300-3303`. Columnas leídas pero nunca escritas: `db/schema.sql:79-82`, leídas en `api/scheduler.py:193`, `:234`; sin ninguna sentencia `UPDATE ... SET autorizacion_activa` en todo el repositorio. Plantilla: `legal/autorizacion-pruebas-seguridad.md`. |
| **1.5 Confidencialidad de las vulnerabilidades encontradas**: NDA + canal seguro de entrega. | **Parcial.** Existe aislamiento multi-tenant real y verificado en la práctica (todo acceso a hallazgos filtra por `tenant_id`; el aislamiento se comprobó end-to-end en una corrida documentada), autenticación JWT y contraseñas con bcrypt. Faltan: (a) **NDA firmado** — la cláusula está redactada (`legal/clausulas-contractuales.md` §3.7) pero sin firmar ni revisar por abogado; (b) el **canal seguro de entrega** ya está definido por escrito —`legal/clausulas-contractuales.md` §3.7.3 obliga a entregar por la plataforma bajo autenticación individual y prohíbe expresamente correo no cifrado, mensajería no corporativa y almacenamiento público— y el producto lo respalda técnicamente: los reportes PDF/DOCX se renderizan en memoria y se transmiten en la respuesta HTTP autenticada, sin quedar como archivo en el servidor. Lo que falta de esta pieza es que la cláusula esté firmada, no que esté escrita; (c) la base no está cifrada en reposo, y el aislamiento es por `WHERE tenant_id = ?` en la aplicación, sin *row-level security* en el motor (limitación asumida y documentada en el propio esquema). | Aislamiento por tenant: `db/schema.sql:10-12` (documenta explícitamente "no con row-level security todavía"); verificación end-to-end en `docs/cumplimiento.md:174-180`. bcrypt: `auth/jwt_auth.py:37-42`. Cláusula de confidencialidad y canal de entrega: `legal/clausulas-contractuales.md` §3.7, §3.7.3. Reportes no persistidos en servidor: `api/main.py:1970`, `:2073`, `:3836`. |

**Resumen sección 1:** 0 Implementado · 4 Parcial · 1 Pendiente.

---

## 2. ISO/IEC 27001:2022 Anexo A — checklist del proveedor

Vigia **no está certificado en ISO/IEC 27001**. Esta es una autoevaluación
contra el Anexo A publicado, útil como insumo de diligencia debida, no como
evidencia de certificación.

| Requisito | Estado real en Vigia | Evidencia |
|---|---|---|
| **A.5 — Política de escaneo ético** (política de seguridad de la información aplicada a la actividad de pruebas). | **Parcial.** El principio "sin autorización no hay escaneo" está *codificado*, que es más fuerte que una política en papel: el gate es código Python determinista sin intervención de LLM, el nodo de bloqueo ni siquiera importa los wrappers de escaneo (es estructuralmente incapaz de escanear), y el propio nodo de escaneo revalida por su cuenta. Además, para escanear un dominio hay que **demostrar control del nombre** por DNS TXT o `.well-known`, no basta declararlo. Desde la revisión anterior el principio también quedó **escrito**: `legal/politica-uso-aceptable.md` es un documento fechado y versionado (v1.0) cuya §3 enumera control por control cómo se hace cumplir la regla, con rutas de código, e incluye una §3.7 de límites conocidos declarados. Falta para cerrar: el documento está en estado **borrador**, sin aprobación formal, sin responsable asignado, con placeholders de razón social y canal de abuso sin llenar, y sin revisión de abogado. Una política sin dueño no es un control de gobierno. | `orchestrator/graph.py:92` (`gate_autorizacion`), `:53` (`bloqueo_autorizacion_node`); `agents/escaneo.py:34-50`; `tools/asset_verification.py`; RoE en `legal/autorizacion-pruebas-seguridad.md`. Política escrita: `legal/politica-uso-aceptable.md` §1, §3 (incluye §3.6 de límites de volumen y ritmo). Placeholders sin llenar: encabezado y §7 del mismo archivo. |
| **A.8 — Clasificar los informes como información confidencial de alto riesgo.** | **Pendiente.** No existe ninguna etiqueta de clasificación de información en el sistema: ni columna en `findings` o `scans`, ni marca de agua o encabezado de clasificación en el reporte generado, ni inventario de activos de información. Los informes se tratan como confidenciales *de facto* (van detrás de JWT y filtrado por tenant), pero eso es control de acceso, no clasificación. | Sin campo de clasificación: `db/schema.sql:127-163` (tabla `findings`), `:101-114` (tabla `scans`). Reporte generado sin encabezado de clasificación: `agents/reporteria.py`, `agents/cumplimiento.py`. Re-verificado en esta revisión: los cambios recientes no tocaron la clasificación de información. |
| **A.9 — Restringir el acceso interno a los hallazgos** (control de acceso basado en necesidad de conocer). | **Parcial.** Existe la primitiva: hay un campo `role` por usuario (`owner` / `admin` / `member`) y un *dependency* `require_role(...)` que devuelve 403. Pero **solo se aplica a los tres endpoints de gestión de invitaciones**. Ningún endpoint de hallazgos, escaneos, reportes o anti-suplantación exige rol: cualquier usuario autenticado del tenant ve todos los hallazgos del tenant. Del lado del proveedor, tampoco hay separación documentada de quién del equipo de Vigia puede acceder a la base de producción. **Matiz nuevo:** el rate limiting agregó una restricción de **volumen** por tenant sobre los endpoints caros, pero es control de abuso y de costo, no control de acceso por necesidad de conocer: no distingue roles y no cambia qué puede ver cada usuario. | `require_role` definido en `auth/jwt_auth.py:107-113`; aplicado únicamente en `api/main.py:3886`, `:3920`, `:3933` (invitaciones). Endpoints de hallazgos con `Depends(get_current_user)` sin rol: `api/main.py` (`GET /findings`, `GET /reports/cumplimiento`, `POST /scan`). Rate limiting por tenant (no por rol): `api/main.py:147`, `:187-191`. |
| **A.12 — Loggear cada escaneo: alcance, horario, resultado** (registro y monitoreo). | **Parcial** — sin cambio de estado, pero con matices nuevos en ambas direcciones. Sigue firme: fila en `scans` con `created_at` / `completed_at` / `estado`, más `trace_log_json` con un evento por nodo del grafo (timestamp UTC ISO), expuesto por dos endpoints de auditoría de solo lectura; las cuatro rutas que disparan un escaneo insertan su fila. **Suma desde la última revisión:** las actividades automáticas de fondo ahora dejan rastro propio en el log de aplicación — el job de purga registra cuántos archivos borró y con qué plazo, y el ciclo recurrente registra los activos omitidos por pausa. **Sigue faltando, re-verificado contra el código actual:** (a) el **alcance** se persiste solo como `target` y `asset_id`; el objeto `Scope` (subdominios, rutas de código, imágenes autorizadas) no se guarda; (b) `scans` **sigue sin columna `user_id`** — se confirmó leyendo el esquema tras los cambios de esta revisión, no se asume; (c) el log de aplicación es `logging` estándar sin retención, rotación ni envío a un almacén append-only, así que sirve para operar pero no como evidencia forense inalterable; (d) no hay monitoreo ni alertas: nadie es notificado si el job de purga deja de correr o si un tenant satura su rate limit. | Inserciones de `scans` en `api/main.py:2206`, `:2892`, `:3314`, `:3510`. Esquema: `db/schema.sql:101-114` (sin `user_id`). Evento con timestamp: `orchestrator/state.py:23-36`. Auditoría: `api/main.py:3741`, `:3765`. `Scope` definido en `orchestrator/state.py:48-59`, no persistido. Log de la purga: `api/scheduler.py:383-387`. Log del ciclo recurrente: `api/scheduler.py:203`, `:213`, `:225`. Logger sin configuración de retención/rotación: `api/main.py:105`. |
| **A.16 — Protocolo si el escaneo causa una caída** (gestión de incidentes de seguridad de la información). | **Pendiente.** No existe ningún runbook, procedimiento escrito ni matriz de escalamiento para el caso en que una prueba de Vigia degrade o tumbe un sistema del cliente. Sí hay mitigaciones *preventivas* reales, y desde la última revisión son más: timeouts por herramienta, presupuesto de tiempo acotado por corrida, tope de escaneos activos simultáneos, rate limiting por tenant en los endpoints que arrancan herramientas pesadas, y throttling del sondeo a terceros. La probabilidad de causar una caída bajó; el procedimiento de qué hacer si ocurre sigue sin existir. Prevenir no es lo mismo que tener un protocolo de respuesta. La obligación contractual correspondiente está redactada en `legal/clausulas-contractuales.md` §3.4, incluida la propuesta de canal de contacto — pero la cláusula describe el compromiso, no el procedimiento interno que lo hace ejecutable. | Sin runbook en el repositorio. Mitigaciones preventivas: `tools/_shared.py:59-73`, `:119-142`; `tools/zap_api.py:503-537`; tope de simultáneos en `tools/zap_api.py:182`; rate limiting en `api/main.py:181-185`; throttling del sondeo en `tools/antisuplantacion.py:109`. Compromiso contractual: `legal/clausulas-contractuales.md` §3.4. |
| **A.17 — Procedimiento para pausar / abortar pruebas.** | **Parcial.** Existe pausa a nivel de programación: `assets.scan_paused` excluye un activo del ciclo recurrente, y es modificable por API. También hay corte duro por timeout y presupuesto de tiempo, y el escaneo activo hace *checkpoint* en base de datos de forma incremental (no se pierde todo si se corta). Se suma ahora una tercera forma de contención preventiva: el escaneo activo reserva un cupo de un semáforo global antes de arrancar, así que el número de pruebas simultáneas contra infraestructura de clientes tiene techo duro y, agotados los cupos, la corrida falla con un error legible en lugar de sumar carga. Eso limita el daño agregado, pero **no es un aborto**. Falta lo que un cliente pediría en una llamada de emergencia: **sigue sin haber forma de detener un escaneo ya en curso** — se re-verificó contra el código actual que no existe endpoint de cancelación ni señal de aborto que el pipeline consulte, y `scan_paused` no afecta a una corrida ya lanzada. Tampoco hay procedimiento escrito de quién puede ordenar el aborto ni en cuánto tiempo debe ejecutarse. | `scan_paused`: `db/schema.sql:95`, escritura en `api/main.py:769-772`, lectura en `api/scheduler.py:203`. Timeouts/presupuesto y checkpoint incremental: `tools/_shared.py:119-142`, `tools/zap_api.py:503-537`. Tope de escaneos simultáneos: `tools/zap_api.py:182` (`turno_de_escaneo_activo`), reservado en `:622`, `:778`. Sin endpoint de cancelación: `grep` de `abortar|cancelar|cancel` sobre `api/main.py` — 0 resultados. |
| **A.18 — Revisión periódica de cambios normativos** (cumplimiento de requisitos legales y contractuales). | **Pendiente.** No hay revisión normativa agendada, ni responsable asignado, ni registro de versiones de la interpretación legal. El proyecto reconoce el problema por escrito —documenta que la Ley 2573 se mapea a nivel de obligaciones en prosa porque no se dispone del texto con numeración de artículos, y recomienda revisión por abogado y auditor certificado— pero reconocer la brecha no es un control. La entrada en vigor plena de la Ley 2573 está prevista hacia noviembre de 2026: hay una fecha concreta que hoy nadie tiene asignada. | Reconocimiento documentado: `docs/cumplimiento.md:34-39`, `:217-219`. Sin tarea, calendario ni responsable en el repositorio. |

**Resumen sección 2:** 0 Implementado · 4 Parcial · 3 Pendiente.

**Resumen global:** 12 controles evaluados — **0 Implementado, 8 Parcial, 4 Pendiente.**

> **Nota sobre el resultado.** El conteo es idéntico al de la revisión anterior
> (0 · 8 · 4) **pese a que aterrizó un lote grande de controles técnicos**:
> rate limiting por tenant, throttling e identificación del bot, tope de
> escaneos simultáneos, purga automática de capturas, verificación de dominio
> para marcas vigiladas y siete documentos legales redactados. Ninguna casilla
> cambió de estado, y eso es informativo, no decepcionante: lo que bloquea cada
> casilla no era el control técnico sino el **componente de gobierno** —
> aprobación, responsable nombrado, firma, procedimiento escrito, monitoreo. La
> sección 3 se actualizó para reflejar qué brechas se cerraron parcialmente.
>
> Que ninguna casilla llegue a "Implementado" no
> significa que no haya controles funcionando: la sección 0.6 lista veintidós
> controles técnicos construidos y verificados, varios de ellos más estrictos
> que lo que un cliente típico esperaría (el gate de autorización es
> estructural, no una validación que se pueda saltar). Lo que falta es
> sistemáticamente la **capa de gobierno**: el documento firmado, la política
> aprobada, el procedimiento escrito, el responsable asignado. Los controles
> técnicos están adelantados respecto de los administrativos, y esa asimetría
> es exactamente lo que la sección 3 propone cerrar.

---

## 3. Brechas abiertas y plan de cierre

Ordenadas por riesgo real, no por facilidad.

### 3.1 Brechas críticas (bloquean una venta a cliente regulado)

**B1 — La firma del contrato no queda registrada en el sistema.**
Las columnas `assets.autorizacion_activa`, `autorizacion_doc_url` y
`autorizado_por` existen (`db/schema.sql:79-82`) y el scheduler **decide con
ellas** si una corrida recurrente puede ser activa (`api/scheduler.py:192`,
`:233`) — pero **ningún endpoint las escribe**. Consecuencia: la única fuente de
verdad sobre "el cliente autorizó" es un booleano autodeclarado en el request
HTTP (`autorizacion_firmada`) más un PDF que vive fuera del sistema. Si mañana
un cliente niega haber autorizado un escaneo activo, el sistema no puede probar
lo contrario por sí solo.
*Cierre:* endpoint `POST /assets/{id}/autorizacion` que escriba las tres
columnas, exija `autorizacion_doc_url` no vacío y `autorizado_por` identificado,
y que `POST /scan/activo` valide contra la columna persistida en lugar del
booleano del request. **Esta es la brecha #1 del proyecto.**

**B2 — La bitácora no registra quién disparó cada escaneo.**
`scans` guarda `tenant_id` pero no `user_id` (`db/schema.sql:102-114`). La
trazabilidad identifica la organización, no a la persona. Para ISO A.12 y para
la obligación de trazabilidad de la Ley 2573 esto es una debilidad directa: un
log sin actor no acredita diligencia individual.
*Cierre:* agregar `user_id TEXT REFERENCES users(id)` a `scans`, poblarlo desde
`UserContext` en las cuatro rutas de inserción (`api/main.py:2098`, `:2779`,
`:3197`, `:3391`), y exponerlo en `GET /audit`. Persistir además el objeto
`scope` completo, no solo `target`, para cerrar el "qué alcance" de A.12.

**B3 — Sin cifrado en reposo de la base de datos.**
La base contiene, para múltiples clientes a la vez, vulnerabilidades explotables
sin parchear, targets, informes completos y screenshots de dominios clon. Es
probablemente el activo de información de mayor riesgo del proyecto y hoy está
en claro. Agrava el hecho de que el aislamiento multi-tenant es por
`WHERE tenant_id = ?` en la aplicación, sin *row-level security* en el motor —
limitación asumida y documentada en `db/schema.sql:10-12`.
*Cierre:* cifrado en reposo a nivel de motor o de volumen en el despliegue
(coordinar con `docs/despliegue.md`), y evaluar RLS de PostgreSQL como defensa
en profundidad para el aislamiento.

### 3.2 Brechas altas

**B4 — Ningún endpoint de hallazgos exige rol (A.9).**
`require_role` existe y funciona (`auth/jwt_auth.py:107-113`) pero solo protege
las invitaciones. Cualquier usuario del tenant ve todos los hallazgos.
*Cierre:* definir qué operaciones exigen `owner`/`admin` (mínimo: lanzar escaneo
activo, exportar informes, gestionar autorizaciones) y aplicar `require_role`.
Documentar además el control de acceso del equipo de Vigia a producción.

**B5 — Sin protocolo escrito de incidentes accidentales (A.16).**
La cláusula contractual promete un comportamiento
(`legal/clausulas-contractuales.md` §3.4) que hoy no tiene procedimiento interno
que lo respalde. Prometer contractualmente algo que no se sabe ejecutar es peor
que no prometerlo.
*Cierre:* runbook de una página — detección, suspensión inmediata de la prueba,
notificación al contacto de emergencia del RoE dentro del plazo pactado,
preservación de logs, informe post-incidente. Debe nombrar responsable y
suplente.

**B6 — No se puede abortar un escaneo en curso (A.17).**
`scan_paused` solo afecta al ciclo recurrente; una corrida ya lanzada no se
detiene. Si un cliente llama en pánico durante una ventana de prueba, hoy la
única respuesta real es esperar al timeout.
*Cierre:* señal de aborto consultada entre etapas del pipeline (el escaneo
activo ya hace checkpoint incremental, así que el punto de corte existe), más
endpoint `POST /scan/{id}/abortar`, más el procedimiento escrito de quién puede
ordenarlo.

**B7 — El cliente no se clasifica como sujeto obligado (Ley 2573, 1.1).**
Sin ese dato, el alcance del servicio no se ajusta y el argumento comercial de
la Ley 2573 se aplica a ciegas — con el riesgo de vendérselo a quien no le
aplica.
*Cierre:* campo estructurado en `tenants` (sector regulado sí/no + tipo),
capturado en el onboarding, reflejado en el Anexo de Alcance del contrato y
usado para condicionar qué advertencias emite el reporte.

### 3.3 Brechas medias

**B8 — Retención: cerrada para capturas, abierta para todo lo demás.**
*Estado: parcialmente cerrada.* Ya existe `legal/politica-de-retencion.md` con
plazos por tipo de dato, y las capturas de dominios clon —el dato de terceros
más sensible que el sistema produce— se purgan solas a los **90 días** mediante
un job periódico de APScheduler (`api/scheduler.py:336`, registrado en `:428`,
plazo en `:361`, configurable por `VIGIA_SCREENSHOT_RETENCION_DIAS`). El plazo
de la política se alineó a la baja, de 180 a 90 días, para coincidir con el
código y por ser el criterio más protector para datos de terceros que nunca
consintieron.
*Lo que sigue abierto:* la purga automática cubre **solo** las capturas.
Hallazgos, trazas de escaneo (`scans.trace_log_json`), reportes DMARC, texto de
reportes finales y cuentas terminadas siguen sin ninguna tarea de vencimiento;
el log de aplicación tampoco tiene retención ni rotación.
*Cierre:* extender el patrón del job de purga a las tablas con plazo declarado
en la Sección 3 de la política, empezando por los hallazgos de perfiles de redes
sociales de terceros (90 días), que son datos personales de terceros igual que
las capturas.

**B9 — Política de escaneo ético: escrita, no aprobada (A.5).**
*Estado: parcialmente cerrada.* El principio está codificado y ahora también
escrito: `legal/politica-uso-aceptable.md` remite al código como evidencia de
aplicación (§3), incluida la §3.6 de límites de volumen, ritmo e identidad del
bot. Falta la capa de gobierno: aprobación formal, responsable nombrado,
placeholders de razón social y canal de abuso llenos, y revisión de abogado.
*Cierre:* aprobar y fechar la política con dueño asignado, y publicar la URL a
la que ya apunta el User-Agent del bot.

**B13 — El User-Agent del bot apunta a una URL no institucional.**
Todo sondeo a dominios de terceros se identifica con una URL donde reclamar
(`tools/antisuplantacion.py:62-71`). Esa URL apunta hoy al repositorio público
del proyecto porque no existe sitio institucional publicado. Es funcional —un
tercero puede leer la política y encontrar el canal de abuso— pero un bot
comercial que se identifica con un repositorio de GitHub es una señal débil de
seriedad frente a un administrador de sistemas.
*Cierre:* publicar la política en un dominio propio y fijar `VIGIA_BOT_USER_AGENT`
a esa URL antes de operar comercialmente.

**B10 — Sin clasificación de información en los informes (A.8).**
*Cierre:* encabezado de clasificación en todo reporte generado y campo de
clasificación en el esquema.

**B11 — Sin revisión normativa periódica (A.18).**
La entrada en vigor plena de la Ley 2573 está prevista hacia noviembre de 2026.
*Cierre:* revisión trimestral con responsable nombrado y registro de versiones;
la primera, antes de esa fecha.

**B12 — `JWT_SECRET` tiene fallback inseguro de desarrollo.**
`auth/jwt_auth.py:28-34` cae a un secreto embebido si la variable de entorno no
está configurada. Está documentado como "solo desarrollo", pero un despliegue
mal configurado arranca igual, en silencio.
*Cierre:* fallar el arranque si `JWT_SECRET` no está definido y el entorno no es
de desarrollo explícito.

### 3.4 Lo que este dossier NO cubre

- Certificación ISO/IEC 27001 (no existe; esto es autoevaluación).
- Auditoría de seguridad independiente del propio Vigia.
- Continuidad de negocio y recuperación ante desastres del servicio.
- Gestión de proveedores propios de Vigia (hosting, proveedor de LLM). Nota
  relevante: el envío de código del cliente a un LLM de terceros ya exige doble
  consentimiento (`api/main.py:2162`), pero no hay acuerdo de tratamiento de
  datos documentado con ese tercero.

---

## 4. Próximos pasos

1. **Revisión por abogado colombiano especializado en derecho digital.** Prioridad máxima, antes de firmar cualquier contrato. Alcance: este dossier y `legal/clausulas-contractuales.md`, con foco en la limitación de responsabilidad (§3.5), la exclusión de sustitución de obligaciones (§3.6) y el rol de Encargado del Tratamiento bajo la Ley 1581 de 2012.
2. **Anexo de Alcance por cliente.** Un RoE firmado por contrato, derivado de `legal/autorizacion-pruebas-seguridad.md`, con activos enumerados uno por uno, ventana horaria, contacto de emergencia y clasificación de sujeto obligado (B7). Lo no listado queda fuera.
3. **Seguro de responsabilidad civil profesional (E&O / cyber liability).** Es lo que un cliente corporativo pregunta apenas ve la cláusula de limitación de responsabilidad. Cotizar con cobertura mínima alineada al tope contractual pactado.
4. **Bitácora de cada escaneo como evidencia de diligencia debida.** Cerrar B1 y B2: sin actor ni constancia de autorización persistida, la bitácora demuestra que algo se ejecutó, pero no que alguien identificable lo autorizó. Es el trabajo de ingeniería con mayor retorno legal del backlog.

---

*Documento de trabajo. Requiere revisión de abogado colombiano especializado en derecho digital antes de uso contractual formal.*
