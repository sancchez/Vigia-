# Revisión de código con IA — Specification

## Purpose

Exponer `agents/revision_ia.py::revisar_codigo()` como un endpoint autenticado
y una UI, con doble consentimiento e ingesta de código sandboxeada por tenant.

## Requirements

### Requirement: Doble consentimiento

El endpoint MUST exigir DOS consentimientos separados antes de leer o enviar
código a un LLM: `autorizacion_firmada` (permiso de pruebas) y
`envio_codigo_a_llm_autorizado` (permiso específico de enviar el contenido del
código a un LLM externo). Si falta cualquiera, el sistema MUST responder 403 y
MUST NOT leer archivos ni invocar el LLM.

#### Scenario: Ambos consentimientos presentes

- GIVEN un usuario autenticado con código subido
- WHEN llama `POST /reports/revision-ia` con ambos flags en `true`
- THEN el sistema invoca `revisar_codigo()` y devuelve 200 con `hallazgos_logica`

#### Scenario: Falta consentimiento de LLM

- GIVEN un request con `autorizacion_firmada=true` y `envio_codigo_a_llm_autorizado=false`
- WHEN llama al endpoint
- THEN el sistema responde 403 con un motivo específico de envío-a-LLM
- AND ningún archivo se lee ni se envía al LLM

#### Scenario: Falta autorización de pruebas

- GIVEN un request con `autorizacion_firmada=false`
- WHEN llama al endpoint
- THEN el sistema responde 403 y no invoca el LLM

### Requirement: Ingesta de código sandboxeada por tenant

El sistema MUST leer código únicamente desde un sandbox propio del tenant
(`uploads/revision_ia/{tenant_id}/{review_id}/`). El sistema MUST NOT aceptar
rutas absolutas arbitrarias del servidor y MUST rechazar cualquier ruta que,
tras resolverse, escape del sandbox del tenant.

#### Scenario: Subida y revisión dentro del sandbox

- GIVEN un usuario que sube uno o más archivos de código
- WHEN el endpoint procesa la subida
- THEN los archivos se guardan bajo el sandbox del tenant
- AND `revisar_codigo()` se invoca sólo con rutas dentro de ese sandbox

#### Scenario: Intento de traversal

- GIVEN una ruta que contiene `..` o apunta fuera del sandbox
- WHEN el endpoint la resuelve
- THEN el sistema la rechaza sin leerla

#### Scenario: Aislamiento entre tenants

- GIVEN el tenant A con código subido
- WHEN el tenant B llama al endpoint
- THEN B nunca puede leer ni referenciar los archivos de A

### Requirement: Persistencia de hallazgos sin schema nuevo

El sistema MUST persistir los hallazgos de lógica de negocio reusando las
tablas `scans` y `findings` (un `scans` sintético + filas `findings` colgadas
de él), sin agregar tablas ni columnas. La severidad `alta|media|baja` MUST
mapearse a `high|medium|low`. Los hallazgos persistidos MUST aparecer en
`GET /findings` del tenant.

#### Scenario: Hallazgos entran al ciclo de vida

- GIVEN una revisión que produce N hallazgos de lógica
- WHEN el endpoint termina
- THEN existe una fila `scans` sintética y N filas `findings` colgadas de ella
- AND cada finding tiene severidad mapeada y su hallazgo original en `raw_json`
- AND aparecen en `GET /findings`

#### Scenario: Revisión sin hallazgos

- GIVEN una revisión que devuelve `hallazgos_logica` vacío
- WHEN el endpoint termina
- THEN no se insertan filas `findings`
- AND la respuesta reporta `archivos_revisados`/`archivos_omitidos`

### Requirement: Degradación cuando el LLM no está disponible

Si `call_claude()` falla o la respuesta no es parseable, el sistema SHOULD
devolver 200 con la lista de hallazgos vacía y los metadatos de la corrida
(archivos revisados/omitidos), sin error 500 — mismo contrato que ya devuelve
la función.

#### Scenario: LLM no disponible

- GIVEN el LLM devuelve error
- WHEN el endpoint invoca `revisar_codigo()`
- THEN responde 200 con `hallazgos_logica` vacío y los metadatos de la corrida

### Requirement: UI de revisión de código

La UI MUST permitir subir o seleccionar código, marcar los dos consentimientos
por separado, y ver los hallazgos de lógica de negocio devueltos. La UI MUST NOT
habilitar el envío hasta que ambos consentimientos estén marcados.

#### Scenario: Flujo de la UI

- GIVEN el usuario abre la vista de revisión de código
- WHEN sube código y marca ambos consentimientos
- THEN el botón de enviar se habilita
- AND al enviar, la UI muestra los hallazgos con archivo, severidad y descripción
