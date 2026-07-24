# Spec: Fase 2b — Tablero de remediación (frontend)

> Nota de alcance: estas specs cubren solo comportamiento observable en
> `frontend/**`. `PATCH /findings/{id}` y la extensión de `GET /findings`
> con `estado`/`owner_user_id`/`sla_due_at` son responsabilidad de
> `fase2a-ciclo-vida-hallazgo` (backend, en diseño paralelo) — aquí se
> especifica lo que el frontend espera y tolera de esos contratos, no su
> implementación real.

## Domain: Frontend — Filtros del tablero de remediación

### Requirement: Filtro por estado

El tablero MUST ofrecer un filtro por `estado` (los 8 valores del CHECK de
`findings.estado`, más una opción "todos"), mismo patrón visual que el filtro
de severidad ya existente (`filtroSev`/`SEV_ORDEN`).

#### Scenario: Filtrar por un estado específico

- GIVEN el tablero tiene hallazgos en varios estados
- WHEN el usuario selecciona el filtro "en_progreso"
- THEN solo se muestran filas con `estado == "en_progreso"`

### Requirement: Filtro por dueño

El tablero MUST ofrecer un filtro por `owner_user_id`, poblado con
`api.listMembers()`, más una opción "todos" y una opción explícita "sin
asignar" para `owner_user_id == null`.

#### Scenario: Filtrar por dueño sin asignar

- GIVEN hay hallazgos con `owner_user_id` nulo
- WHEN el usuario selecciona el filtro "sin asignar"
- THEN solo se muestran filas con `owner_user_id == null`

### Requirement: Filtro por proximidad de SLA

El tablero MUST ofrecer un filtro de tres valores: "vencido", "próximo",
"ok" (incluye sin SLA), más "todos".

#### Scenario: Filtrar por SLA vencido

- GIVEN hay hallazgos con `sla_due_at` en el pasado y otros en el futuro
- WHEN el usuario selecciona el filtro "vencido"
- THEN solo se muestran filas cuyo `sla_due_at` es anterior al momento actual

## Domain: Frontend — Badges de SLA

### Requirement: Clasificación de proximidad de SLA

El sistema MUST clasificar cada hallazgo en exactamente una de tres
categorías visuales a partir de `sla_due_at`: `vencido` (fecha ya pasó),
`proximo` (vence en ≤3 días desde ahora), `ok` (vence en >3 días). Un
hallazgo sin `sla_due_at` MUST clasificarse como `ok` (no como error ni como
vencido) y MAY mostrar un badge neutro ("sin SLA") en vez de "ok" para no
sugerir falsamente que hay un plazo cumplido.

#### Scenario: SLA vencido

- GIVEN un hallazgo con `sla_due_at` anterior a la hora actual
- WHEN se renderiza su badge de SLA
- THEN el badge usa la variante roja/alerta con texto que indica vencimiento

#### Scenario: SLA próximo a vencer

- GIVEN un hallazgo con `sla_due_at` dentro de los próximos 3 días
- WHEN se renderiza su badge de SLA
- THEN el badge usa la variante ámbar con el número de días restantes

#### Scenario: SLA sin definir

- GIVEN un hallazgo con `sla_due_at` nulo
- WHEN se renderiza su badge de SLA
- THEN el badge NO usa la variante roja ni ámbar (se muestra neutro o "sin SLA")

## Domain: Frontend — Cambio de estado/dueño desde la UI

### Requirement: Actualización de estado y dueño vía `PATCH /findings/{id}`

El tablero MUST permitir cambiar `estado` y `owner_user_id` de un hallazgo
individual mediante controles inline (select por fila), enviando la
solicitud a `api.patchFinding(id, cambios)`. Tras una respuesta exitosa, la
UI MUST reflejar el nuevo valor (por refetch de la lista o actualización
local del item).

#### Scenario: Cambio de estado exitoso

- GIVEN un hallazgo en estado "triage"
- WHEN el usuario cambia el select de estado a "asignado" y la API responde 200
- THEN la fila muestra "asignado" tras la actualización

### Requirement: Manejo de error del PATCH

Si `api.patchFinding` falla (red, 4xx, 5xx), el sistema MUST mostrar un
mensaje de error visible al usuario (mismo patrón que `mensaje`/`ApiError`
ya usado en `Dashboard.tsx`) y MUST NOT dejar el control inline mostrando el
valor nuevo como si se hubiera guardado — el select MUST revertir (o
permanecer) en el valor confirmado por el servidor, no en el valor
optimista no confirmado.

#### Scenario: PATCH falla por error de red

- GIVEN el usuario cambia el estado de un hallazgo
- WHEN `api.patchFinding` rechaza con un error
- THEN se muestra un mensaje de error
- AND el select vuelve a mostrar el estado anterior confirmado, no el que el usuario intentó asignar

## Domain: Frontend — Tolerancia a contrato de lectura incompleto

### Requirement: Degradación cuando `GET /findings` no expone los campos nuevos

Mientras `fase2a-ciclo-vida-hallazgo` no extienda `GET /findings` con
`estado`/`owner_user_id`/`sla_due_at`, el frontend MUST tratar su ausencia
como valores por defecto (`estado` ausente → tratar como `"descubierto"`
solo a efectos de filtro/badge, sin asumirlo como dato real confirmado;
`owner_user_id`/`sla_due_at` ausentes → `null`) y MUST NOT lanzar error ni
dejar la vista en blanco. El tablero MAY mostrarse con filtros deshabilitados
o con una nota indicando datos incompletos en ese caso.

#### Scenario: Backend aún no expone los campos nuevos

- GIVEN `GET /findings` devuelve items sin `estado`/`owner_user_id`/`sla_due_at`
- WHEN el Dashboard renderiza el tablero
- THEN la vista se renderiza sin errores, con badges/filtros en su estado por defecto (sin SLA, sin dueño)
