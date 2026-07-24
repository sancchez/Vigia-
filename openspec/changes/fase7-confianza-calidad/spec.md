# Spec: Confianza y calidad — evals de agentes IA y registro de auditoría

> Nota de dependencia cruzada (no bloqueante para esta spec, sí para
> `sdd-apply`): los endpoints de auditoría se montan sobre `api/main.py` y la
> UI sobre `Dashboard.tsx`, compartidos con changes hermanos. Las specs de
> abajo son aditivas y no dependen del contenido de esos changes.

## Domain: API — Registro de auditoría por tenant

### Requirement: Exponer el `trace_log` persistido como auditoría de solo lectura

El sistema MUST exponer `GET /scans/{scan_id}/trace` que devuelve el
`trace_log` ya persistido en `scans.trace_log_json` para ese scan, scoped al
`tenant_id` del usuario autenticado. Cada entrada MUST incluir al menos
`timestamp`, `agente`, `accion`, `resultado` (forma de `TraceEvent`).

El endpoint MUST ser de solo lectura: no expone ninguna operación de
escritura, edición ni borrado sobre el `trace_log`.

#### Scenario: Traza de un scan propio

- GIVEN un scan `S1` del tenant autenticado con `trace_log` no vacío
- WHEN el usuario llama `GET /scans/S1/trace`
- THEN el sistema devuelve la lista de eventos en orden cronológico
- AND cada evento incluye `timestamp`, `agente`, `accion`, `resultado`

#### Scenario: Scan de otro tenant

- GIVEN un scan `S2` pertenece a un tenant distinto
- WHEN el usuario llama `GET /scans/S2/trace`
- THEN el sistema MUST responder 404 (mismo criterio que `GET /scans/{id}`)

#### Scenario: Scan con traza vacía

- GIVEN un scan `S3` cuyo `trace_log_json` es `[]`
- WHEN se llama `GET /scans/S3/trace`
- THEN el sistema devuelve una lista vacía con 200 (no error)

### Requirement: Registro de auditoría agregado por tenant

El sistema MUST exponer `GET /audit` (solo lectura, scoped por `tenant_id`)
que lista los eventos de auditoría de todos los scans del tenant, cada uno
correlacionado con su `scan_id` y `target`, ordenados del más reciente al más
antiguo, con paginación/límite.

#### Scenario: Auditoría multi-scan

- GIVEN el tenant tiene 2 scans con eventos de traza
- WHEN se llama `GET /audit`
- THEN la respuesta incluye eventos de ambos scans, cada uno con su `scan_id`
- AND están ordenados del más reciente al más antiguo

#### Scenario: Aislamiento entre tenants

- GIVEN el tenant A y el tenant B tienen scans con traza
- WHEN el usuario del tenant A llama `GET /audit`
- THEN la respuesta MUST NOT incluir ningún evento de scans del tenant B

## Domain: Eval — Barrera de regresión y métrica de falsos positivos

### Requirement: Métrica de tasa de falsos positivos

El harness `eval/run_eval.py` MUST exponer una tasa de falsos positivos
(FP-rate) derivada de los TP/FP que ya calcula, además de precisión/recall/F1,
en su reporte y en una salida parseable por máquina.

#### Scenario: FP-rate en el reporte

- GIVEN una corrida con `TP` y `FP` conocidos
- WHEN se ejecuta `run_eval.py`
- THEN el reporte incluye `fp_rate = FP / (TP + FP)` junto a las métricas
  existentes

### Requirement: Barrera de regresión automatizada contra baseline

El sistema MUST proveer un runner de regresión que compara las métricas de una
corrida contra un `baseline.json` versionado y MUST fallar (exit code distinto
de cero) si el recall baja o el FP-rate sube más allá de un umbral definido.

La actualización del baseline MUST ser un paso explícito e intencional (no
automático) — coherente con `eval/prompt_versions/README.md`.

#### Scenario: Regresión detectada

- GIVEN un `baseline.json` con recall=0.90
- WHEN una corrida nueva da recall=0.80 (caída > umbral)
- THEN el runner MUST fallar con exit code distinto de cero y nombrar la
  métrica degradada

#### Scenario: Corrida dentro de umbral

- GIVEN un baseline y una corrida con métricas iguales o mejores
- WHEN se corre el runner
- THEN termina con éxito y NO reescribe el baseline por sí solo

## Domain: Eval — Calidad de agentes (fallback público)

### Requirement: Evals de calidad del fallback público, sin asertar IP privada

El sistema MUST proveer evals que midan la calidad de los agentes de
priorización, remediación y revisión IA corriendo ÚNICAMENTE contra su modo
`fallback_publico` (`_MODO == "fallback_publico"`). Estos evals MUST NOT
asertar el contenido exacto de los prompts privados de `vigia_core_private/`.

Los evals MUST verificar, como mínimo determinista: conformidad del esquema
JSON de salida de cada agente, y ausencia de marcadores de alucinación /
formato inválido. La priorización de fallback MUST verificarse por su
propiedad declarada (orden por severidad técnica).

#### Scenario: Esquema de salida de priorización (fallback)

- GIVEN el módulo de priorización en modo `fallback_publico`
- WHEN se evalúa su salida sobre un dataset de hallazgos verificados
- THEN cada objeto cumple `{hallazgo_id, urgencia_negocio, justificacion}`
- AND el orden respeta la severidad técnica reportada

#### Scenario: No asertar contenido privado

- GIVEN la suite pública de evals de calidad
- WHEN se ejecuta en una máquina sin `vigia_core_private` instalado
- THEN todos los evals de calidad pasan sin depender de categorías/prompts
  privados

## Domain: Frontend — Timeline de auditoría de solo lectura

### Requirement: Vista de auditoría de solo lectura

El frontend MUST mostrar el registro de auditoría del tenant como una línea de
tiempo de solo lectura (evento: `timestamp`, `agente`, `accion`, `resultado`),
sin controles de edición ni borrado.

#### Scenario: Render de la traza de un scan

- GIVEN un scan con eventos de traza
- WHEN el usuario abre la vista de auditoría
- THEN ve los eventos en orden cronológico, sin ningún control de mutación

#### Scenario: Scan sin traza

- GIVEN un scan con `trace_log` vacío
- WHEN se abre su vista de auditoría
- THEN se muestra un estado "sin traza registrada" sin romper la UI
