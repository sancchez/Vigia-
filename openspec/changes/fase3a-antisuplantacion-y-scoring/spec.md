# Spec: Fase 3a — Anti-suplantación bajo demanda + scoring de dominios clon

## Domain: API — Disparo bajo demanda (`POST /antisuplantacion`)

### Requirement: Ejecutar anti-suplantación sobre un asset dominio verificado

El sistema MUST exponer `POST /antisuplantacion`, autenticado y scoped a
`tenant_id`, que recibe el `asset_id` (o `valor`) de un dominio del tenant. El
asset objetivo MUST estar registrado y verificado (`verificado=1` o exento) por
ese tenant; de lo contrario responde 403. El endpoint MUST crear una fila `scans`
real, correr la rama anti-suplantación (`agents/antisuplantacion.py`),
enriquecer cada dominio clon con scoring, y persistir los hallazgos vía el
ciclo de vida existente (`_registrar_findings_con_ciclo_de_vida`).

#### Scenario: Dominio verificado dispara la protección

- GIVEN un asset `tipo=dominio`, `verificado=1` del tenant
- WHEN el usuario llama `POST /antisuplantacion` con ese `asset_id`
- THEN responde 200 con `scan_id` y la lista de dominios clon detectados (cada uno con `score`)

#### Scenario: Asset no verificado se rechaza

- GIVEN un asset dominio con `verificado=0` y no exento
- WHEN se llama `POST /antisuplantacion`
- THEN responde 403 y no se crea ningún `finding`

#### Scenario: Asset de otro tenant

- WHEN se llama `POST /antisuplantacion` con un `asset_id` de otro tenant
- THEN responde 404

## Domain: Scoring de infraestructura activa

### Requirement: Puntaje transparente 0-4 por dominio clon

Por cada dominio lookalike, el sistema MUST calcular un `score` entero de 0 a 4
contando cuántas de estas señales de infraestructura activa responden: registro
DNS-A, registro MX, handshake SSL exitoso, respuesta HTTP(S) viva. El desglose
booleano por señal MUST guardarse en `findings.raw_json`. Una señal que no
responde o cuya herramienta no está instalada MUST contar como 0 (no romper).

#### Scenario: Dominio con las cuatro señales activas

- GIVEN un dominio clon con A, MX, SSL y HTTP vivos
- WHEN se calcula el scoring
- THEN `score = 4` y `raw_json.scoring` registra las cuatro señales en `true`

#### Scenario: Dominio parqueado sin servicios

- GIVEN un dominio clon que resuelve A pero sin MX/SSL/HTTP
- WHEN se calcula el scoring
- THEN `score = 1`

#### Scenario: Probe MX no disponible degrada, no rompe

- GIVEN el resolver DNS de MX no está instalado
- WHEN se calcula el scoring
- THEN la señal MX cuenta como `false` y el scoring de las demás señales continúa

### Requirement: Screenshot best-effort del dominio clon

El sistema SHOULD capturar un screenshot del dominio clon como evidencia de
contenido clonado. Si el navegador headless no está disponible, el sistema MUST
continuar sin screenshot (persistir el hallazgo con `screenshot=null`), sin error.

#### Scenario: Screenshot omitido cuando el navegador no está instalado

- GIVEN Playwright/chromium no instalado
- WHEN se procesa un dominio clon
- THEN el hallazgo se persiste con `screenshot_path=null` y el resto del flujo no falla

## Domain: Lectura del panel (`GET /antisuplantacion/dominios`)

### Requirement: Listar dominios clon con evidencia y takedown

El sistema MUST exponer `GET /antisuplantacion/dominios`, scoped a `tenant_id`,
que devuelve los `findings` `tipo=dominio_variante`/`perfil_red_social` del
tenant con: `endpoint` (dominio/URL), `severidad`, `estado`, `score`, desglose de
scoring, `screenshot` (path o null), y el borrador de takedown (`analisis_claude`).
El sistema MUST exponer el screenshot binario vía una ruta scoped a tenant.

#### Scenario: Panel lista clones del tenant

- GIVEN el tenant tiene 3 dominios clon persistidos
- WHEN llama `GET /antisuplantacion/dominios`
- THEN responde 200 con 3 items, cada uno con `score` y `estado`

#### Scenario: Screenshot de otro tenant no es accesible

- WHEN se pide el screenshot de un finding de otro tenant
- THEN responde 404

## Domain: KPIs de marca (`GET /antisuplantacion/kpis`)

### Requirement: MTTD (best-effort) y TTTD (exacto)

El sistema MUST exponer `GET /antisuplantacion/kpis`, scoped a `tenant_id`, que
calcula en Python (portable SQLite/Postgres, sin funciones de fecha SQL):
- **TTTD**: por cada dominio clon `cerrado`, `created_at` de su primera fila en
  `finding_transiciones` con `estado_nuevo='cerrado'` menos `primera_vez_visto`.
  Precisión `exacto`. Findings sin cierre MUST excluirse del promedio.
- **MTTD**: `primera_vez_visto` menos la fecha `NotBefore` del certificado SSL
  del clon (guardada en `raw_json` durante el scoring). Precisión `aproximado`.
  Clones sin SSL capturado MUST excluirse del promedio (no cuentan como 0).

#### Scenario: TTTD exacto con un clon cerrado

- GIVEN un dominio clon con transición a `cerrado` 5 días tras `primera_vez_visto`
- WHEN llama `GET /antisuplantacion/kpis`
- THEN `tttd_dias ≈ 5` y `precision="exacto"`

#### Scenario: MTTD null sin evidencia de infraestructura

- GIVEN ningún clon tiene `NotBefore` de SSL capturado
- WHEN se calcula MTTD
- THEN `mttd_dias=null` (no se promedia sobre datos ausentes)

#### Scenario: KPIs vacíos sin dominios clon

- GIVEN el tenant no tiene dominios clon
- WHEN llama `GET /antisuplantacion/kpis`
- THEN responde 200 con `tttd_dias=null` y `mttd_dias=null`, sin error
