# Spec: Fase 1 — Bucle de Detección visible

> Nota de alcance: `estado_verificacion` como columna SQL dedicada en
> `findings` es responsabilidad de **fase0-fundaciones-datos** (dependencia
> cruzada, no bloqueante). Estas specs asumen que, hasta que esa columna
> exista, `estado_verificacion`/`cves_detectados`/`evidencia_exploitdb` se
> derivan leyendo `findings.raw_json` en el momento de servir la respuesta.

## Domain: API — Drill-down de hallazgo (`GET /findings/{id}`)

### Requirement: Detalle curado de un hallazgo por id

El sistema MUST exponer `GET /findings/{id}` que devuelve un payload curado
derivado de la fila `findings` y de su `raw_json`, scoped al `tenant_id` del
usuario autenticado (mismo patrón que `GET /scans/{scan_id}`).

El payload MUST incluir: `id`, `scan_id`, `tipo`, `severidad`, `endpoint`,
`confirmado`, `created_at` (ya expuestos hoy en `FindingOut`), más
`descripcion`, `solucion`, `referencia`, `cwe` (derivados de los campos
nativos de la alerta dentro de `raw_json`, cuando existan), y
`estado_verificacion`, `cves_detectados`, `evidencia_exploitdb` (derivados de
los campos que ya produce `agents/verificacion.py` dentro de `raw_json`).

El endpoint MUST NOT devolver `raw_json` crudo tal cual como campo de nivel
superior del contrato HTTP — solo los campos curados listados arriba.

#### Scenario: Dueño del hallazgo pide su detalle

- GIVEN un finding con id `F1` pertenece al tenant del usuario autenticado
- WHEN el usuario llama `GET /findings/F1` con un JWT válido
- THEN la respuesta es 200 con los campos curados descritos arriba
- AND si `raw_json` tiene `description`/`solution`/`reference`/`cweid`, esos valores aparecen en `descripcion`/`solucion`/`referencia`/`cwe`

#### Scenario: Finding de otro tenant

- GIVEN un finding con id `F2` pertenece a un tenant distinto del usuario autenticado
- WHEN el usuario llama `GET /findings/F2`
- THEN la respuesta es 404 (no 403 — MUST NOT confirmar la existencia del recurso a un tenant ajeno)

#### Scenario: Id inexistente

- GIVEN ningún finding tiene id `F3`
- WHEN el usuario llama `GET /findings/F3`
- THEN la respuesta es 404

#### Scenario: Sin autenticación

- WHEN se llama `GET /findings/{id}` sin JWT o con uno inválido
- THEN la respuesta es 401

## Domain: Verificación — Escaneo activo

### Requirement: Verificación determinista aplicada antes de persistir

Cada alerta capturada por `tools/zap_api.py::run_checkpointed_active_scan`
(vía `get_alerts()`) MUST pasar por la misma lógica determinista de
`agents/verificacion.py` (expuesta como función pública) antes del
`INSERT INTO findings` dentro de `_correr_escaneo_activo_en_background`.
El resultado (booleano `confirmado` + `estado_verificacion` +
`cves_detectados` + `evidencia_exploitdb`) MUST persistirse: `confirmado`
en su columna existente, y el resto dentro de `raw_json`.

`estado_verificacion` SHALL tomar únicamente uno de estos tres valores:
`confirmado_via_exploitdb`, `no_verificable_sin_cve`,
`no_confirmado_cve_sin_poc_publica`.

Un hallazgo cuyo texto (nombre, descripción, referencia) no contiene ningún
patrón `CVE-YYYY-NNNN` MUST terminar en `no_verificable_sin_cve`. Este
resultado MUST NOT tratarse como fallo del sistema ni de la verificación —
es el resultado correcto para hallazgos de configuración (CSP, clickjacking,
headers faltantes) que legítimamente no tienen CVE asociado.

#### Scenario: Alerta ZAP con CVE confirmable

- GIVEN una alerta ZAP cuyo `description` o `reference` contiene un CVE con PoC pública indexada en Exploit-DB
- WHEN `_correr_escaneo_activo_en_background` persiste esa alerta
- THEN la fila `findings` resultante tiene `confirmado=1` y `raw_json.estado_verificacion == "confirmado_via_exploitdb"`
- AND `raw_json.evidencia_exploitdb` contiene al menos un `{cve, edb_id, descripcion}`

#### Scenario: Alerta ZAP de configuración sin CVE

- GIVEN una alerta ZAP de tipo configuración (ej. "CSP header not set") sin ningún CVE en su texto
- WHEN se persiste
- THEN `confirmado=0` y `raw_json.estado_verificacion == "no_verificable_sin_cve"`

#### Scenario: Alerta ZAP con CVE sin PoC pública

- GIVEN una alerta ZAP que referencia un CVE real pero sin entrada en el índice local de Exploit-DB
- WHEN se persiste
- THEN `confirmado=0` y `raw_json.estado_verificacion == "no_confirmado_cve_sin_poc_publica"`

## Domain: Inteligencia de explotación visible

### Requirement: Evidencia Exploit-DB en el detalle del hallazgo

Cuando `evidencia_exploitdb` (dentro de `raw_json`) no está vacío, el
payload de `GET /findings/{id}` MUST incluir esa lista, cada entrada con
`edb_id` y `descripcion` legibles.

#### Scenario: Hallazgo con PoC pública

- GIVEN un finding cuyo `raw_json.evidencia_exploitdb` tiene 2 entradas
- WHEN se llama `GET /findings/{id}` para ese finding
- THEN la respuesta incluye una lista `evidencia_exploitdb` con las 2 entradas, cada una con `edb_id` y `descripcion`

#### Scenario: Hallazgo sin evidencia

- GIVEN un finding cuyo `raw_json.evidencia_exploitdb` es `[]` o no existe
- WHEN se llama `GET /findings/{id}`
- THEN la respuesta incluye `evidencia_exploitdb: []`

## Domain: Frontend — Panel de detalle y chip explicado

### Requirement: Panel de detalle accesible por click de fila

El Dashboard MUST abrir un panel de detalle al hacer click en cualquier fila
de hallazgo, tanto en la tabla general de hallazgos como en la tabla de
detalle de un scan. El panel MUST mostrar descripción, solución, endpoint,
severidad, tipo, el chip de verificación explicado, y — cuando aplique —
la evidencia Exploit-DB.

#### Scenario: Click en fila de la tabla general

- GIVEN el usuario está en el Dashboard con hallazgos cargados
- WHEN hace click en una fila de la tabla de hallazgos
- THEN se abre el panel de detalle con la información de ese hallazgo obtenida de `GET /findings/{id}`

### Requirement: Chip de verificación explicado (no binario)

El chip que hoy muestra solo "confirmado"/"sin confirmar" MUST mostrar
texto distinto para cada uno de los 3 valores de `estado_verificacion`:
"confirmado vía Exploit-DB", "no verificable (sin CVE)", "config — sin PoC
pública" (o equivalentes igual de explícitos).

#### Scenario: Hallazgo de configuración sin CVE

- GIVEN un finding con `estado_verificacion == "no_verificable_sin_cve"`
- WHEN se renderiza su fila o su panel de detalle
- THEN el chip muestra un texto que indica explícitamente que no aplica CVE (no solo "sin confirmar" genérico)
