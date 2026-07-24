# Spec: Verificación de cierre y KPIs de remediación

> Nota de dependencia cruzada (no bloqueante para esta spec, sí para
> `sdd-apply`): las specs de esta sección asumen que `fase2a-ciclo-vida-
> hallazgo` ya provee las transiciones de `estado` (incluido `verificado` y
> `cerrado`) vía `PATCH /findings/{id}` o equivalente. Si `fase2a` no
> persiste un historial con timestamp exacto de transición, la Requirement
> "MTTR aproximado" de este documento sigue siendo válida tal cual está
> escrita (ya asume aproximación), no requiere cambios.

## Domain: API — Verificación de cierre (`POST /findings/{id}/verificar-cierre`)

### Requirement: Re-escaneo de un endpoint puntual, no del asset completo

El sistema MUST exponer `POST /findings/{id}/verificar-cierre` que
re-escanea ÚNICAMENTE el `endpoint` del hallazgo indicado (no otros
endpoints del mismo asset), scoped al `tenant_id` del usuario autenticado.

El sistema MUST determinar el modo de re-escaneo por la herramienta de
origen del hallazgo (`herramienta` en `raw_json` cuando exista, o alertas
ZAP planas para hallazgos de escaneo activo): ZAP usa el modo single-target
nuevo de `tools/zap_api.py`; nuclei re-invoca `run_nuclei` sobre el
endpoint exacto.

#### Scenario: Hallazgo ya no reproduce en el re-escaneo puntual

- GIVEN un finding `F1` (severidad `high`, origen ZAP, `estado=mitigado`)
  del tenant autenticado
- WHEN el usuario llama `POST /findings/F1/verificar-cierre`
- THEN el sistema re-escanea solo `F1.endpoint` (sin recorrer el resto del
  asset)
- AND si la alerta original ya no aparece, el sistema transiciona `F1` a
  `verificado` y luego a `cerrado` automáticamente
- AND la respuesta incluye el `estado` final y un resumen del re-escaneo

#### Scenario: Hallazgo persiste en el re-escaneo puntual

- GIVEN un finding `F2` con `estado=mitigado`
- WHEN se llama `POST /findings/F2/verificar-cierre`
- THEN si la alerta sigue apareciendo, `F2.estado` MUST NOT transicionar
  a `verificado` ni `cerrado`
- AND la respuesta MUST indicar que el hallazgo sigue presente

#### Scenario: Hallazgo de otro tenant

- GIVEN un finding `F3` pertenece a un tenant distinto del usuario
  autenticado
- WHEN se llama `POST /findings/F3/verificar-cierre`
- THEN el sistema MUST responder 404 (no 403), mismo criterio que
  `GET /findings/{id}`

### Requirement: Origen sin endpoint HTTP re-escaneable

Para hallazgos cuya `herramienta` sea `semgrep`, `trivy` o `grype` (código
o imagen, no URL), el sistema MUST NOT intentar un re-escaneo HTTP
puntual y MUST responder con un error explícito indicando que este tipo de
hallazgo no soporta verificación de cierre automática por endpoint.

#### Scenario: Verificación de cierre sobre hallazgo SAST

- GIVEN un finding `F4` con `herramienta=semgrep`
- WHEN se llama `POST /findings/F4/verificar-cierre`
- THEN el sistema MUST responder 422 con un mensaje explicando la
  limitación (no debe fingir soporte ni devolver un falso "verificado")

### Requirement: Falla de infraestructura de escaneo no corrompe el estado

Si el daemon de ZAP (o el binario de nuclei) no está disponible, el
sistema MUST dejar `estado` sin cambios y MUST devolver un error claro —
nunca marcar `verificado`/`cerrado` por default ante una falla.

#### Scenario: ZAP no disponible durante la verificación

- GIVEN Docker/ZAP no responde al arrancar el daemon
- WHEN se llama `POST /findings/{id}/verificar-cierre` sobre un hallazgo
  de origen ZAP
- THEN el sistema MUST responder con error (5xx o 503) sin alterar
  `estado`

## Domain: API — KPIs de remediación (`GET /kpis`)

### Requirement: MTTR aproximado por severidad

El sistema MUST exponer `GET /kpis` (solo lectura, scoped por
`tenant_id`) con MTTR (mean time to remediate) agrupado por severidad,
calculado sobre hallazgos con `estado IN ('verificado','cerrado')`.

El sistema MUST marcar el MTTR como aproximado (campo `precision:
"aproximado"` o equivalente) cuando no exista un timestamp exacto de
transición a cierre, usando `ultima_vez_visto`/`created_at` como sustituto
documentado — nunca debe presentarse como un dato exacto si no lo es.

#### Scenario: MTTR con datos disponibles

- GIVEN 3 hallazgos `critical` cerrados con `created_at` y
  `ultima_vez_visto` conocidos
- WHEN se llama `GET /kpis`
- THEN el sistema devuelve el MTTR promedio para `critical` en días,
  marcado como aproximado

#### Scenario: Sin hallazgos cerrados de una severidad

- GIVEN el tenant no tiene hallazgos `low` en `verificado`/`cerrado`
- WHEN se llama `GET /kpis`
- THEN el MTTR de `low` MUST devolverse como `null`, no `0` ni omitido

### Requirement: Porcentaje dentro de SLA

El sistema MUST calcular el % de hallazgos abiertos (`estado` distinto de
`cerrado`/`aceptado_riesgo`) cuyo `sla_due_at` aún no venció, sobre el
total de hallazgos abiertos del tenant.

#### Scenario: Todos los hallazgos abiertos dentro de plazo

- GIVEN 5 hallazgos abiertos con `sla_due_at` futuro
- WHEN se llama `GET /kpis`
- THEN `porcentaje_dentro_sla` MUST ser 100

#### Scenario: Sin hallazgos abiertos

- GIVEN el tenant no tiene hallazgos abiertos
- WHEN se llama `GET /kpis`
- THEN `porcentaje_dentro_sla` MUST devolverse como `null` (no división
  por cero)

### Requirement: Tendencia de riesgo por activo

El sistema MUST exponer, por asset del tenant, una serie temporal de
conteo de hallazgos abiertos por severidad a lo largo de los escaneos
(`scans.asset_id` → `scans.created_at`), para graficar tendencia.

#### Scenario: Activo con varios escaneos históricos

- GIVEN un asset con 3 scans completados en fechas distintas
- WHEN se llama `GET /kpis`
- THEN la respuesta incluye una serie por ese asset con un punto por
  scan (fecha + conteo por severidad)

## Domain: Herramientas — Modo single-target (`tools/zap_api.py`)

### Requirement: Escaneo activo de ZAP sin recorrer el sitio completo

`ascan_start` MUST aceptar un parámetro `recurse` (default `True` para no
romper el uso existente); cuando se invoque con `recurse=False`, el
escaneo activo MUST limitarse al nodo/URL exacta pasada, sin expandir a
otras URLs del sitio.

#### Scenario: Verificación de cierre invoca ascan sin recurse

- GIVEN un endpoint puntual `https://ejemplo.com/login`
- WHEN el flujo de verificación de cierre llama al modo single-target
- THEN ZAP MUST recibir `recurse=false` y el resultado MUST limitarse a
  ese endpoint
