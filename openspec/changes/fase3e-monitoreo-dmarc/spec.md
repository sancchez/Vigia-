# Spec: Monitoreo DMARC

> Dominio nuevo (no hay spec previa de DMARC). Se consume la tabla
> `dmarc_reports` (Fase 0) tal cual; sin columnas ni tabla nuevas. Ninguna
> dependencia de runtime con `fase3a`/`fase3b` — es complemento, no consumidor.

## Domain: Herramientas — Parser de reportes DMARC (`tools/dmarc.py`)

### Requirement: Descompresión y parseo seguro de un reporte agregado

El sistema MUST aceptar los bytes de un reporte DMARC agregado en `.xml`,
`.xml.gz` o `.zip`, descomprimir cuando aplique, y parsear el XML con un
parser resistente a entidades externas/expansión (defusedxml). El sistema
MUST NOT usar `xml.etree.ElementTree` directo sobre entrada externa.

El parser MUST ser tolerante: campos opcionales ausentes usan defaults, y un
reporte con estructura parcial se guarda igual con su `raw_json` completo.

#### Scenario: Reporte gzip válido de Google

- GIVEN los bytes de un `google.com!ejemplo.com!...xml.gz` estándar RFC 7489
- WHEN se parsea
- THEN devuelve metadatos (`org`, `dominio`, `rango_inicio`, `rango_fin`) y
  una lista de `records` (IP origen, conteo, disposition, spf/dkim alineados)

#### Scenario: XML con entidad externa maliciosa (XXE)

- GIVEN un XML que declara una entidad externa o una expansión recursiva
- WHEN se parsea
- THEN el parser MUST rechazarlo sin leer archivos ni colgarse (defusedxml
  lanza una excepción controlada), y el llamador la traduce a error 422

#### Scenario: Archivo que no es un reporte DMARC

- GIVEN un XML bien formado pero sin la raíz `<feedback>` de RFC 7489
- WHEN se parsea
- THEN el sistema MUST señalar reporte inválido (no persistir una fila vacía)

### Requirement: Agregación de remitentes no autorizados

El sistema MUST proveer, dado un conjunto de reportes de un dominio, una
agregación por IP de origen con: mensajes totales, veredicto DMARC efectivo, y
un flag `autorizado`. Una IP MUST marcarse `autorizado=false` cuando su correo
falla la alineación DMARC (ni SPF ni DKIM alineados con el dominio).

#### Scenario: IP que falla SPF y DKIM

- GIVEN records donde `10.0.0.9` tiene `spf=fail` y `dkim=fail`
- WHEN se agrega
- THEN `10.0.0.9` aparece con `autorizado=false` (remitente no autorizado)

#### Scenario: IP legítima del propio cliente

- GIVEN records donde `20.1.1.1` tiene `dkim=pass` alineado
- WHEN se agrega
- THEN `20.1.1.1` aparece con `autorizado=true`

## Domain: API — Ingesta y vista DMARC (`api/main.py`)

### Requirement: Upload manual de un reporte (`POST /dmarc/reports`)

El sistema MUST exponer `POST /dmarc/reports` que recibe un archivo
(`.xml`/`.xml.gz`/`.zip`), lo parsea con `tools/dmarc.py`, y persiste una fila
en `dmarc_reports` con `tenant_id` del usuario autenticado, `dominio`,
`rango_inicio`, `rango_fin` y el `raw_json` normalizado.

#### Scenario: Ingesta exitosa

- GIVEN un usuario autenticado y un `.xml.gz` válido
- WHEN llama `POST /dmarc/reports`
- THEN se crea una fila `dmarc_reports` scoped a su `tenant_id`
- AND la respuesta incluye el `id`, `dominio` y el número de records ingeridos

#### Scenario: Archivo inválido o XML peligroso

- GIVEN un archivo que no parsea o dispara la protección XXE
- WHEN llama `POST /dmarc/reports`
- THEN el sistema MUST responder 422 sin persistir ninguna fila

### Requirement: Listado de reportes (`GET /dmarc/reports`)

El sistema MUST exponer `GET /dmarc/reports` (solo lectura, scoped por
`tenant_id`) que devuelve los reportes ingeridos con dominio, rango de fechas
y conteos, ordenados por `created_at` desc.

#### Scenario: Aislamiento por tenant

- GIVEN el tenant B tiene reportes y el usuario pertenece al tenant A
- WHEN el usuario A llama `GET /dmarc/reports`
- THEN los reportes del tenant B MUST NOT aparecer en la respuesta

### Requirement: Vista de remitentes no autorizados (`GET /dmarc/summary`)

El sistema MUST exponer `GET /dmarc/summary` (solo lectura, scoped por
`tenant_id`) que agrega sobre los `raw_json` de todos los reportes del tenant
(opcionalmente filtrado por `dominio`) y devuelve la lista de IPs de origen
con mensajes totales, veredicto DMARC y `autorizado`, resaltando las no
autorizadas.

#### Scenario: Resumen con remitentes mixtos

- GIVEN reportes con IPs legítimas y una IP que falla DMARC
- WHEN se llama `GET /dmarc/summary`
- THEN la IP que falla aparece como remitente no autorizado con su conteo
- AND las IPs legítimas aparecen marcadas `autorizado=true`

#### Scenario: Tenant sin reportes

- GIVEN el tenant no ha ingerido ningún reporte
- WHEN se llama `GET /dmarc/summary`
- THEN el sistema MUST devolver una lista vacía (no error)

## Domain: Frontend — Panel DMARC (`DmarcPanel.tsx`)

### Requirement: Vista de reportes y remitentes no autorizados

El frontend MUST montar un panel que permita subir un reporte y muestre los
reportes ingeridos y la tabla de remitentes no autorizados, sin romper el
`Dashboard.tsx` existente.

#### Scenario: Usuario sube un reporte y ve el resumen

- GIVEN un usuario autenticado en el Dashboard
- WHEN selecciona un archivo de reporte y lo sube
- THEN el panel refresca la lista de reportes y la tabla de remitentes,
  resaltando las IPs no autorizadas
