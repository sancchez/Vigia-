# Spec: Fase 2a — Ciclo de vida del hallazgo

## Domain: API — Transiciones de estado (`PATCH /findings/{id}`)

### Requirement: Transición de estado validada por máquina de estados

El sistema MUST exponer `PATCH /findings/{id}`, scoped a `tenant_id` (mismo
patrón 404 que `GET /findings/{id}`), que acepta cambios parciales de
`estado`, `owner_user_id`, y una `nota` de texto libre obligatoria cuando se
envía `estado`.

Las transiciones válidas de `estado` SHALL ser únicamente: `descubierto→triage`,
`triage→asignado`, `asignado→en_progreso`, `en_progreso→mitigado`,
`mitigado→verificado`, `verificado→cerrado`, y desde cualquier estado
distinto de `cerrado`/`aceptado_riesgo` hacia `aceptado_riesgo` (salida
lateral). Cualquier otra transición (saltos hacia adelante, retrocesos,
transición desde `cerrado`) MUST rechazarse con 409.

Cada cambio de `estado` u `owner_user_id` aplicado MUST registrar una fila en
`finding_transiciones` con `estado_anterior`, `estado_nuevo` (o null si solo
cambió el owner), `owner_anterior`, `owner_nuevo`, `nota`, `user_id` de quien
hizo el cambio, y timestamp.

#### Scenario: Transición válida con nota

- GIVEN un finding en `descubierto` del tenant del usuario
- WHEN llama `PATCH /findings/{id}` con `{"estado": "triage", "nota": "revisado por seguridad"}`
- THEN la respuesta es 200 con `estado: "triage"` y se crea una fila en `finding_transiciones`

#### Scenario: Transición inválida

- GIVEN un finding en `descubierto`
- WHEN llama `PATCH /findings/{id}` con `{"estado": "cerrado"}`
- THEN la respuesta es 409 y `estado` no cambia

#### Scenario: Salida lateral a aceptado_riesgo desde cualquier estado activo

- GIVEN un finding en `en_progreso`
- WHEN llama `PATCH /findings/{id}` con `{"estado": "aceptado_riesgo", "nota": "riesgo aceptado por negocio"}`
- THEN la respuesta es 200

#### Scenario: Nota obligatoria en cambio de estado

- GIVEN un finding en `triage`
- WHEN llama `PATCH /findings/{id}` con `{"estado": "asignado"}` sin `nota`
- THEN la respuesta es 422

#### Scenario: Reasignar dueño sin cambiar estado

- GIVEN un finding sin `owner_user_id`
- WHEN llama `PATCH /findings/{id}` con `{"owner_user_id": "<user_id_del_tenant>"}`
- THEN la respuesta es 200, `owner_user_id` queda seteado, y no se exige `nota`

#### Scenario: Owner debe pertenecer al tenant

- GIVEN un `user_id` que no pertenece al tenant del finding
- WHEN se envía como `owner_user_id`
- THEN la respuesta es 422

#### Scenario: Finding de otro tenant

- WHEN se llama `PATCH /findings/{id}` sobre un finding de otro tenant
- THEN la respuesta es 404

## Domain: SLA por severidad

### Requirement: `sla_due_at` calculado en ambos paths de inserción

Al insertar un finding nuevo (tanto en `POST /scan` como en
`POST /scan/activo`), el sistema MUST calcular `sla_due_at = created_at + N
días` según `severidad`, usando la config del tenant si existe o el default:
`critical`→7, `high`→30, `medium`→90, `low`/`info`→`null` (sin plazo).

#### Scenario: Finding crítico recibe SLA de 7 días

- GIVEN un tenant sin config de SLA propia
- WHEN se inserta un finding con `severidad="critical"`
- THEN `sla_due_at` queda igual a `created_at` + 7 días

#### Scenario: Finding de severidad baja sin plazo

- WHEN se inserta un finding con `severidad="low"` o `"info"`
- THEN `sla_due_at` es `null`

#### Scenario: Config de SLA propia del tenant

- GIVEN un tenant con SLA custom para `high` = 15 días
- WHEN se inserta un finding `severidad="high"` de ese tenant
- THEN `sla_due_at` usa 15 días, no el default de 30

## Domain: Detección de reincidencia (drift)

### Requirement: Comparar contra hallazgos existentes del mismo asset

Al insertar findings de un scan sobre un asset con escaneos previos, el
sistema MUST comparar cada hallazgo nuevo contra los existentes del mismo
`asset_id` (vía `scans.asset_id`) por `tipo`+`endpoint`. Si hay match:
MUST actualizar `ultima_vez_visto` del finding existente en vez de insertar
un duplicado.

### Requirement: Reapertura de hallazgo reincidente

Si el finding existente que hizo match está en `cerrado`, `verificado` o
`aceptado_riesgo`, el sistema MUST volver su `estado` a `descubierto` y
registrar una fila en `finding_transiciones` con nota automática indicando
reincidencia.

#### Scenario: Hallazgo repetido en segundo scan actualiza, no duplica

- GIVEN un finding `tipo="XSS"`, `endpoint="/login"` del asset A, visto en el scan 1
- WHEN el scan 2 sobre el mismo asset A detecta el mismo `tipo`+`endpoint`
- THEN no se crea una fila nueva en `findings`; `ultima_vez_visto` del finding existente se actualiza al `created_at` del scan 2

#### Scenario: Hallazgo cerrado reaparece

- GIVEN un finding `tipo="XSS"`, `endpoint="/login"` en `estado="cerrado"`
- WHEN un scan nuevo sobre el mismo asset detecta el mismo `tipo`+`endpoint`
- THEN el finding vuelve a `estado="descubierto"` y se registra la transición con nota de reincidencia

#### Scenario: Scan sin asset resoluble no participa del drift

- GIVEN un `scans.asset_id` es `null` (scan histórico anterior a este change)
- WHEN se calcula drift para un scan nuevo
- THEN ese scan viejo se ignora en la comparación (no genera falsos matches ni excepciones)

#### Scenario: Hallazgo nuevo sin match previo

- GIVEN ningún finding existente del asset coincide en `tipo`+`endpoint`
- WHEN se inserta
- THEN se crea una fila nueva con `primera_vez_visto = ultima_vez_visto = created_at`
