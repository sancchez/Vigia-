# Spec: Fase 3b+3c — Vigilancia continua (panel CertStream + feed self-hosted)

> Nota de alcance: el panel (3b, frontend) consume `GET /findings` (ya expone
> `tipo`, `estado`, `owner_user_id`, `sla_due_at`, `endpoint`) y el CRUD nuevo
> de `watched_brands`. El feed self-hosted (3c) es infra Docker. No se toca
> `vigia_core_private/`.

## Domain: Backend — CRUD de watched_brands

### Requirement: Listar marcas vigiladas del tenant

`GET /watched-brands` MUST devolver solo las marcas del tenant autenticado
(aislamiento por `tenant_id`), más recientes primero, incluyendo `is_active`.

#### Scenario: Listado aislado por tenant

- GIVEN el tenant A tiene 2 marcas y el tenant B tiene 1
- WHEN el usuario del tenant A llama `GET /watched-brands`
- THEN recibe exactamente sus 2 marcas y ninguna del tenant B

### Requirement: Crear una marca vigilada

`POST /watched-brands` con `{ marca }` MUST crear una fila `watched_brands`
para el tenant. La `marca` MUST ser no vacía tras trim; el sistema MUST
rechazar (422) una marca vacía. Un duplicado exacto (misma `marca` activa del
tenant) SHOULD reactivarse/deduplicarse en vez de crear otra fila.

#### Scenario: Alta válida

- GIVEN un tenant autenticado
- WHEN envía `POST /watched-brands` con `{ "marca": "bancoacme" }`
- THEN se crea la marca con `is_active=1` y se devuelve con su `id`

#### Scenario: Marca vacía rechazada

- GIVEN un tenant autenticado
- WHEN envía `POST /watched-brands` con `{ "marca": "   " }`
- THEN responde 422 y no crea ninguna fila

### Requirement: Desactivar una marca vigilada

`DELETE /watched-brands/{id}` MUST desactivar (soft-delete: `is_active=0`) o
borrar la marca del tenant. Sobre una marca de otro tenant MUST responder 404.

#### Scenario: Baja de marca ajena bloqueada

- GIVEN una marca que pertenece al tenant B
- WHEN el usuario del tenant A hace `DELETE /watched-brands/{id_de_B}`
- THEN responde 404 y la marca de B queda intacta

## Domain: Listener — Consumo de watched_brands

### Requirement: Las marcas configuradas se vigilan de verdad

`_build_variant_map()` MUST incorporar las `watched_brands` activas de cada
tenant al mapa de variantes, de forma que un certificado de una permutación de
una marca configurada produzca un finding `dominio_variante_certstream`. Una
marca cuya generación de variantes falle MUST aislarse (log + continuar) sin
tumbar el mapa ni el listener, igual que hoy hace con los activos de dominio.

#### Scenario: Marca añadida entra al mapa tras el refresh

- GIVEN un tenant con la marca "acme" activa
- WHEN el listener (re)construye el mapa de variantes
- THEN las variantes derivadas de "acme" quedan en el mapa asociadas a ese tenant

#### Scenario: Marca inválida no rompe el mapa

- GIVEN una marca que hace fallar la generación de variantes
- WHEN se construye el mapa
- THEN esa marca se omite con log y el resto del mapa se construye normal

## Domain: Frontend — Panel de vigilancia continua

### Requirement: Lista de dominios clon detectados

El panel MUST mostrar los findings con `tipo == "dominio_variante_certstream"`
del tenant, con al menos: dominio base, dominio observado, cuándo se detectó
(`created_at`), severidad y `estado`. MUST reusar el lenguaje visual de
`RemediationBoard.tsx` (tabla + chips + badge de SLA), no una UI nueva.

#### Scenario: Se muestran solo findings de CertStream

- GIVEN el tenant tiene findings de escaneo web y findings CertStream
- WHEN se renderiza el panel de vigilancia
- THEN solo aparecen las filas con `tipo == "dominio_variante_certstream"`

#### Scenario: Panel vacío sin ruido

- GIVEN el tenant no tiene ningún finding CertStream
- WHEN se renderiza el panel
- THEN se muestra un estado vacío ("sin dominios clon detectados"), no un error

### Requirement: Ciclo de vida reutilizado (no modelo nuevo)

Cada dominio clon MUST tratarse como un finding con su `estado`/`owner`/
`sla_due_at`; el panel MAY permitir cambiar `estado`/`owner` reusando
`api.patchFinding` (mismo contrato de Fase 2a/2b). El panel MUST NOT introducir
un endpoint o tabla de "dominios" separada.

#### Scenario: Cambio de estado de un dominio clon

- GIVEN un dominio clon en estado "descubierto"
- WHEN el usuario lo pasa a "triage" y el PATCH responde 200
- THEN la fila refleja "triage" tras el refetch

## Domain: Frontend — Configuración de watched_brands

### Requirement: Alta y baja de marcas desde la UI

La UI MUST permitir agregar una marca (input + submit → `POST /watched-brands`)
y desactivar una existente (→ `DELETE`), refrescando la lista tras cada cambio
exitoso (patrón `cargarTodo()`). Un error de la API MUST mostrarse al usuario
sin dejar la vista rota.

#### Scenario: Alta refleja la nueva marca

- GIVEN la sección de marcas vigiladas
- WHEN el usuario agrega "acme" y la API responde 200
- THEN "acme" aparece en la lista sin recargar la página

## Domain: Infra — Feed CertStream self-hosted (3c)

### Requirement: docker-compose para certstream-server-go

MUST existir un `docker-compose` que levante `certstream-server-go` exponiendo
un endpoint websocket local, y la documentación MUST indicar cómo apuntar
`VIGIA_CERTSTREAM_URL` a esa instancia. Si el feed es inalcanzable, el listener
MUST seguir degradando con gracia (comportamiento ya existente) sin tumbar la API.

#### Scenario: Feed levantado y apuntado

- GIVEN `certstream-server-go` corriendo vía el compose
- WHEN `VIGIA_CERTSTREAM_URL` apunta a su websocket y arranca la API
- THEN el listener se conecta a ese feed en vez del público muerto

#### Scenario: Feed caído no rompe la API

- GIVEN `VIGIA_CERTSTREAM_URL` apunta a un feed inalcanzable
- WHEN arranca la API
- THEN el listener registra el fallo y se apaga solo; el resto de la API sigue arriba
