# Spec: Endurecimiento para producción (Fase 6)

> Organizado en tres bloques que corresponden 1:1 con los sub-changes
> recomendados en `proposal.md` (`fase6a-operacion-produccion`,
> `fase6b-billing-wompi`, `fase6c-onboarding-y-export`). Cada bloque es
> aplicable de forma independiente. `fase6b` está **gated**: su `sdd-apply`
> no debe correr sin consentimiento explícito del usuario en chat (ver
> `HANDOFF.md` línea 165).
>
> Nota de dependencia cruzada (no bloqueante para esta spec, sí para
> `sdd-apply`): asume que Fase 0 dejó `assets.autorizacion_activa` y la
> tabla `subscriptions` con planes `trial/costero/flota/armada`, y que
> fase2c dejó `GET /kpis` y `tools/report_export.py`.

---

# Bloque 6a — Operación en producción

## Domain: LLM de producción — Monitoreo de costo (`agents/_llm.py`)

### Requirement: Registro de uso por llamada al LLM

`call_claude` MUST registrar, por cada llamada resuelta contra un backend
de **API** (Anthropic o OpenAI), el proveedor, el model id, los tokens de
entrada y salida, el costo estimado y un identificador de contexto
(tenant y, cuando aplique, `scan_id`/reporte). El registro MUST persistirse
en una tabla `llm_usage` scoped por `tenant_id`.

El registro MUST NOT romper el contrato de retorno actual de `call_claude`
(sigue devolviendo solo el texto) ni requerir cambios en los 7 call sites
existentes.

#### Scenario: Llamada vía API de Anthropic con key configurada

- GIVEN `ANTHROPIC_API_KEY` configurada y un contexto de tenant `T1`
- WHEN un agente invoca `call_claude(system, user)`
- THEN el sistema persiste una fila en `llm_usage` con `provider="anthropic"`,
  el model id, tokens in/out y `costo_estimado` en USD
- AND el texto de la respuesta se devuelve igual que antes

#### Scenario: Fallback CLI no reporta costo medible

- GIVEN no hay API key y el sistema cae a `claude -p` (CLI)
- WHEN un agente invoca `call_claude(system, user)`
- THEN el sistema MUST registrar el uso con `provider="cli"` y
  `costo_estimado=null` (o marcarlo "no medible"), nunca inventar un número
- AND MUST NOT fallar la llamada por no poder medir el costo

### Requirement: Costo agregado por reporte y por tenant

El sistema MUST exponer un endpoint de solo lectura (`GET /costos` o
`GET /usage`), scoped por `tenant_id`, que agregue el costo estimado por
reporte/scan y el total del tenant en un rango de tiempo.

#### Scenario: Un reporte con varias llamadas al LLM

- GIVEN un scan `S1` de `T1` que disparó 4 llamadas al LLM registradas
- WHEN se llama `GET /costos`
- THEN la respuesta incluye el costo agregado de `S1` (suma de las 4) y el
  total del tenant

#### Scenario: Aislamiento por tenant

- GIVEN uso de LLM registrado para `T1` y `T2`
- WHEN un usuario de `T1` llama `GET /costos`
- THEN MUST devolverse únicamente el uso de `T1`

## Domain: Escaneo activo programado seguro (`api/scheduler.py`)

### Requirement: Escaneo activo recurrente gated por autorización persistente

El ciclo recurrente MUST disparar escaneo **activo** (ZAP/Nuclei vía
`tools/zap_api.py`) únicamente sobre activos con
`assets.autorizacion_activa = 1`. Para el resto, MUST conservar el
comportamiento actual (solo recon pasivo, o ser omitido si no está
verificado). El gate MUST evaluarse leyendo la columna persistida, nunca un
booleano recibido en un request.

#### Scenario: Activo con autorización activa persistida

- GIVEN un asset `A1` con `verificado=1` y `autorizacion_activa=1`
- WHEN corre `run_scan_cycle_once()`
- THEN el sistema ejecuta escaneo activo sobre `A1`
- AND persiste el scan con `autorizacion_firmada=1`

#### Scenario: Activo verificado pero sin autorización activa

- GIVEN un asset `A2` con `verificado=1` y `autorizacion_activa=0`
- WHEN corre `run_scan_cycle_once()`
- THEN el sistema MUST ejecutar solo recon pasivo sobre `A2` (como hoy)
- AND MUST NOT ejecutar escaneo activo sobre `A2`

#### Scenario: Activo no verificado

- GIVEN un asset `A3` con `verificado=0` y no exento
- WHEN corre `run_scan_cycle_once()`
- THEN `A3` MUST ser omitido por completo del ciclo (mismo gate de
  verificación que hoy)

### Requirement: Frecuencia de escaneo configurable por activo

El sistema MUST permitir una frecuencia de escaneo por activo (además del
intervalo global `VIGIA_SCAN_INTERVAL_HOURS`). Un activo MUST poder
marcarse como pausado (no se le corre ningún ciclo).

#### Scenario: Activo pausado

- GIVEN un asset `A4` con programación en estado pausado
- WHEN corre el ciclo recurrente
- THEN `A4` MUST ser omitido (ni activo ni pasivo)

#### Scenario: Configurar frecuencia desde la API

- GIVEN un usuario autenticado dueño del asset `A1`
- WHEN fija la frecuencia de `A1` vía la API de programación
- THEN el sistema persiste la frecuencia scoped al tenant
- AND un asset de otro tenant MUST responder 404

---

# Bloque 6b — Facturación Wompi (GATED)

> **No aplicar sin consentimiento explícito del usuario en chat.** Ver
> `HANDOFF.md` línea 165. Esta spec describe el comportamiento objetivo; su
> implementación queda pendiente de luz verde.

## Domain: Billing — Suscripción Wompi (`api/main.py`, `subscriptions`)

### Requirement: Inicio de checkout de suscripción

El sistema MUST exponer un endpoint autenticado que inicie el checkout de
Wompi para un plan válido (`costero|flota|armada`) y devuelva los datos
necesarios para redirigir al pago, sin exponer la `WOMPI_PRIVATE_KEY` al
cliente.

#### Scenario: Checkout de un plan válido

- GIVEN un tenant `T1` en plan `trial`
- WHEN inicia checkout para el plan `costero`
- THEN el sistema devuelve la referencia/URL de pago de Wompi
- AND `subscriptions.estado` de `T1` pasa a `pendiente`

#### Scenario: Plan inválido

- GIVEN un tenant autenticado
- WHEN inicia checkout para un plan que no existe
- THEN el sistema MUST responder 422 sin llamar a Wompi

### Requirement: Webhook de confirmación de pago con firma verificada

El sistema MUST exponer un webhook que reciba eventos de Wompi, verifique
la firma con `WOMPI_EVENTS_SECRET`, y actualice `subscriptions.estado`
(`activa`/`cancelada`) y `renovacion_en`. Eventos con firma inválida MUST
rechazarse (4xx) sin alterar estado.

#### Scenario: Evento de pago aprobado con firma válida

- GIVEN un evento Wompi de transacción aprobada con firma válida para `T1`
- WHEN llega al webhook
- THEN `subscriptions.estado` de `T1` pasa a `activa` y `plan` al comprado
- AND `renovacion_en` se fija según el ciclo

#### Scenario: Evento con firma inválida

- GIVEN un evento con firma que no valida contra `WOMPI_EVENTS_SECRET`
- WHEN llega al webhook
- THEN el sistema MUST responder 4xx y MUST NOT alterar `subscriptions`

---

# Bloque 6c — Onboarding y export ejecutivo

## Domain: Onboarding de verificación de dominio (frontend + `api/main.py`)

### Requirement: Flujo guiado de verificación de propiedad de dominio

El sistema MUST ofrecer un flujo de onboarding que guíe al usuario paso a
paso por la verificación DNS-TXT o archivo HTTP de un asset tipo dominio,
reusando la lógica existente (`tools/asset_verification.py`,
`GET /assets` con `instrucciones_verificacion`, `POST /assets/{id}/verify`).
La lógica de verificación backend MUST NOT reimplementarse — el flujo solo
la orquesta y la presenta.

#### Scenario: Verificación DNS-TXT exitosa guiada

- GIVEN un asset dominio `D1` no verificado
- WHEN el usuario sigue el wizard, publica el TXT indicado y pide verificar
- THEN el sistema llama `POST /assets/{id}/verify` con `metodo=dns_txt`
- AND al verificarse, el wizard muestra `D1` como `verificado` y ofrece el
  siguiente paso (autorizar escaneo activo)

#### Scenario: Token aún no propagado

- GIVEN un asset dominio `D2` cuyo registro TXT todavía no propaga
- WHEN el usuario pide verificar
- THEN el wizard MUST mostrar el resultado negativo del backend con
  instrucciones claras, sin marcar `verificado` ni bloquear el reintento

## Domain: Export ejecutivo consolidado (`api/main.py`, `tools/report_export.py`)

### Requirement: Reporte ejecutivo multi-activo descargable

El sistema MUST exponer un endpoint autenticado que genere un reporte
ejecutivo **consolidado sobre todos los activos del tenant** (no un solo
scan), agregando postura de riesgo por activo, KPIs de remediación (reuso
de `GET /kpis` de fase2c) y hallazgos abiertos por severidad, descargable
como PDF o DOCX vía `tools/report_export.py`.

#### Scenario: Export ejecutivo con varios activos

- GIVEN un tenant `T1` con 3 activos escaneados y KPIs disponibles
- WHEN se llama al endpoint de export ejecutivo con `formato=pdf`
- THEN el sistema devuelve un PDF con una sección por activo más un resumen
  consolidado (KPIs + hallazgos abiertos por severidad)

#### Scenario: Formato inválido

- GIVEN un tenant autenticado
- WHEN se pide el export con un `formato` distinto de `pdf`/`docx`
- THEN el sistema MUST responder 422 (mismo criterio que los downloads de
  reporte ya existentes)

#### Scenario: Aislamiento por tenant

- GIVEN activos de `T1` y `T2`
- WHEN un usuario de `T1` pide el export ejecutivo
- THEN el reporte MUST incluir únicamente activos y datos de `T1`
