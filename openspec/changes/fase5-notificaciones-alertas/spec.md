# Spec: Notificaciones y alertas

> Nota de dependencia cruzada (no bloqueante para esta spec, sí a considerar en
> `sdd-apply`): el evento `dominio_clon_detectado` se ancla al finding
> `tipo='dominio_variante_certstream'` que `api/certstream_listener.py` YA
> escribe hoy. `fase3b` (en paralelo, no visible) puede afinar ese contrato;
> si cambia el `tipo`/severidad, solo se ajusta el predicado de disparo, no la
> forma del despacho.

## Domain: Despacho de notificaciones (`api/notificaciones.py`)

### Requirement: Envío regido por `notification_rules` del tenant

El sistema MUST enviar una notificación solo cuando exista una fila activa en
`notification_rules` para ese `tenant_id` y ese `evento`. El destino y el canal
salen de la regla, no de constantes. Si no hay regla activa, el sistema MUST NOT
enviar nada (silencio, no error).

#### Scenario: Tenant con regla activa para el evento

- GIVEN un tenant con `notification_rules(evento='hallazgo_critico_nuevo', canal='email', destino='ops@x.co', is_active=1)`
- WHEN se dispara `hallazgo_critico_nuevo` para ese tenant
- THEN el sistema envía un email a `ops@x.co`
- AND registra una fila en `notifications` con `enviado_en` poblado

#### Scenario: Tenant sin regla para el evento

- GIVEN un tenant sin fila activa en `notification_rules` para el evento
- WHEN se dispara ese evento
- THEN el sistema MUST NOT enviar nada y MUST NOT registrar en `notifications`

### Requirement: Idempotencia por recurso

El sistema MUST NOT enviar dos veces la misma notificación para el mismo
`tenant_id + evento + recurso` (p.ej. mismo `finding_id`, o mismo `finding_id`
+ umbral de SLA). Antes de enviar, MUST consultar `notifications` por esa clave.

#### Scenario: Segundo disparo del mismo recurso

- GIVEN ya se envió y registró `hallazgo_critico_nuevo` para el finding `F1`
- WHEN se re-evalúa el mismo `F1` (re-escaneo, reinicio del proceso)
- THEN el sistema MUST NOT re-enviar el email para `F1`

### Requirement: Envío best-effort, aislado del flujo llamante

Un fallo de SMTP (host caído, credenciales malas, timeout) MUST NOT propagarse
al path que originó el evento (persistencia de findings, ciclo del scheduler).
El error MUST loguearse; la fila en `notifications` MUST quedar con `enviado_en`
NULL para indicar intento fallido.

#### Scenario: SMTP no disponible al enviar

- GIVEN el servidor SMTP configurado no responde
- WHEN se dispara un evento con regla activa
- THEN el escaneo/scheduler que lo originó continúa sin excepción
- AND queda registro en `notifications` con `enviado_en` NULL

#### Scenario: SMTP no configurado

- GIVEN no hay variables de entorno SMTP definidas
- WHEN se dispara un evento con regla activa
- THEN el sistema loguea la omisión y no lanza excepción

## Domain: Eventos disparadores

### Requirement: Hallazgo crítico nuevo

El sistema MUST disparar `hallazgo_critico_nuevo` cuando se persiste un finding
con `severidad='critical'` que es nuevo (no una reaparición ya notificada), en
el mismo path de inserción de findings.

#### Scenario: Finding critical recién insertado

- GIVEN un scan que produce un finding `severidad='critical'` nuevo
- WHEN se persiste el finding
- THEN se evalúa y (si hay regla) se despacha `hallazgo_critico_nuevo`

#### Scenario: Finding no crítico

- GIVEN un finding `severidad='high'` o menor
- WHEN se persiste
- THEN el sistema MUST NOT disparar `hallazgo_critico_nuevo`

### Requirement: Dominio clon detectado

El sistema MUST disparar `dominio_clon_detectado` cuando se persiste un finding
de tipo de dominio clon (v1: `tipo='dominio_variante_certstream'`).

#### Scenario: CertStream escribe un dominio variante

- GIVEN el listener persiste un finding `tipo='dominio_variante_certstream'`
- WHEN se completa la inserción
- THEN se evalúa y (si hay regla) se despacha `dominio_clon_detectado`

### Requirement: Escalamiento de SLA (75% y vencido)

El sistema MUST evaluar periódicamente los findings abiertos (`estado` distinto
de `cerrado`/`verificado`/`aceptado_riesgo`) con `sla_due_at` no nulo, y
disparar `sla_escalamiento` en dos umbrales: al alcanzar el 75% del plazo
(entre `created_at` y `sla_due_at`) y al vencer (`sla_due_at` en el pasado).
Cada umbral se notifica UNA sola vez por finding.

#### Scenario: Finding cruza el 75% del plazo

- GIVEN un finding abierto cuyo tiempo transcurrido ≥ 75% del plazo y `sla_due_at` futuro
- WHEN corre el job de escalamiento
- THEN se despacha `sla_escalamiento` con umbral `75pct`, una sola vez

#### Scenario: Finding vencido

- GIVEN un finding abierto con `sla_due_at` en el pasado
- WHEN corre el job
- THEN se despacha `sla_escalamiento` con umbral `vencido`, una sola vez (independiente del aviso de 75%)

#### Scenario: Finding cerrado antes del umbral

- GIVEN un finding que pasó a `cerrado` antes de alcanzar el 75%
- WHEN corre el job
- THEN el sistema MUST NOT despachar ningún escalamiento para ese finding

## Domain: Configuración de reglas (`api/main.py` + frontend)

### Requirement: CRUD de `notification_rules` scoped por tenant

El sistema MUST exponer endpoints para listar, crear, activar/desactivar y
eliminar reglas de notificación, todos scoped al `tenant_id` del usuario
autenticado. Una regla ajena al tenant MUST responder 404.

#### Scenario: Crear una regla

- GIVEN un usuario autenticado
- WHEN hace `POST` de una regla `{evento, canal:'email', destino}`
- THEN se crea con `tenant_id` del usuario y `is_active=1`

#### Scenario: Regla de otro tenant

- GIVEN una regla que pertenece a otro tenant
- WHEN el usuario intenta modificarla/eliminarla
- THEN el sistema MUST responder 404 (mismo criterio que `GET /findings/{id}`)

### Requirement: Canal validado; email en v1

El sistema MUST aceptar `canal='email'` en v1. Otros canales (`slack`,
`webhook`) MAY reservarse en el esquema pero MUST responder un error explícito
si se intenta crear una regla con un canal no soportado todavía.

#### Scenario: Regla con canal no soportado

- GIVEN un intento de crear una regla `canal='slack'`
- WHEN se envía el `POST`
- THEN el sistema MUST responder 422 indicando que solo `email` está soportado en v1
