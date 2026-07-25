# Decisión: persistencia de credenciales de prueba por asset (Tarea 3.2)

> Documento de decisión, escrito ANTES de implementar, como exige
> `tasks.md` 3.2. Son secretos de un tercero (el cliente da a Vigia un
> usuario/contraseña de prueba de su propia app para que el escaneo
> autenticado los use) — no pueden ir en texto plano en la base de datos.

## Qué existe hoy en el repo (investigado, no asumido)

- **No hay ningún mecanismo de cifrado reversible en el repo.** `bcrypt`
  (`auth/jwt_auth.py:14,37-42`) es hash de una sola vía para contraseñas de
  usuarios de Vigia — sirve para verificar login, no para recuperar el valor
  original. Las credenciales de prueba de un asset SÍ necesitan poder
  recuperarse en texto plano en el momento del escaneo (hay que mandárselas
  de verdad a `POST /rest/user/login` del target) — bcrypt no aplica.
- **No está instalado `cryptography`** ni ninguna librería de cifrado
  simétrico (`tools/requirements.txt` solo lista `dnstwist`,
  `sherlock-project`, `semgrep`, `requests`). Sería una dependencia nueva.
- **Precedente de "secreto en env var, nunca en DB"**: `JWT_SECRET`
  (`auth/jwt_auth.py:28-34`) — se lee de `os.environ`, con un fallback
  inseguro solo para dev local, y jamás se persiste en la base de datos.
  Este es el patrón más cercano a reutilizar: una master key fuera de la DB.
  Lo mismo aplica a `WOMPI_PRIVATE_KEY`/`WOMPI_EVENTS_SECRET`
  (`docs/produccion-readiness.md:89`) — todos los secretos actuales del
  sistema son de infraestructura propia, viven en variables de entorno, y
  ninguno hoy es "un secreto de un tercero que hay que guardar y volver a
  usar" como este caso.
- **Aislamiento por tenant**: ya existe y es consistente en todo el schema —
  toda tabla de negocio (`assets`, `scans`, `findings`, etc.) tiene
  `tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE`
  (`db/schema.sql`), y cada query de lectura filtra explícitamente por
  `WHERE tenant_id = ?` contra el `tenant_id` del JWT del usuario
  autenticado (patrón visible en `listar_assets`, `verificar_asset`, etc.,
  `api/main.py`). El modelo de `asset` hoy (`db/schema.sql:67-82`) es plano:
  id, tenant_id, tipo, valor, notas, flags de verificación y de
  autorización — no tiene ningún campo de credenciales todavía.

## Decisión

### Dónde viven
Tabla nueva **`asset_credentials`**, separada de `assets` (no columnas
inline). Motivo: un `SELECT *` o un dump completo de `assets` (que se usa
para listar/mostrar en el panel) nunca debe poder arrastrar secretos por
accidente — mantenerlos en su propia tabla hace que cualquier código que no
la consulte explícitamente sea seguro por default.

```
CREATE TABLE IF NOT EXISTS asset_credentials (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    asset_id            TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    login_url           TEXT NOT NULL,   -- ej. '/rest/user/login', no asumir path fijo
    username_encriptado BLOB NOT NULL,
    password_encriptado BLOB NOT NULL,
    creado_por          TEXT NOT NULL,   -- user_id de quien la cargó
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    rotated_at          TIMESTAMP,
    UNIQUE(asset_id)                     -- una credencial de prueba activa por asset
);
```

`ON DELETE CASCADE` en ambas FK, igual que el resto del schema — borrar el
asset o el tenant borra la credencial automáticamente, no queda huérfana.

### Cómo se cifran
Cifrado simétrico reversible con **Fernet** (`cryptography.fernet`, paquete
nuevo `cryptography` a agregar a `tools/requirements.txt` o a un
`requirements.txt` de `api/` si se separa). Fernet da AES-128 en modo CBC +
HMAC autenticado en un solo token — evita implementar cifrado a mano y ya es
el estándar de facto en Python para "cifrar un secreto pequeño con una master
key".

- **Master key**: variable de entorno nueva, `VIGIA_CREDENTIALS_KEY`, mismo
  patrón que `JWT_SECRET` — generada una vez (`Fernet.generate_key()`),
  vive en el `.env`/secret manager del despliegue, **nunca en la base de
  datos ni en el repo**. Sin fallback inseguro para producción (a diferencia
  de `JWT_SECRET`, que sí tiene un fallback dev-only): si
  `VIGIA_CREDENTIALS_KEY` no está seteada, la función de cifrado debe fallar
  duro (`RuntimeError`), no degradar a un valor conocido — un secreto de
  tercero cifrado con una key adivinable es peor que no tener el feature.
- Username y password se cifran por separado (dos columnas), no concatenados,
  para poder rotar o mostrar-parcialmente uno sin tocar el otro.

### Quién puede leerlas
- El texto plano descifrado **nunca sale de dentro del proceso backend, y
  solo dentro del thread que arma el escaneo autenticado**
  (`_correr_escaneo_activo_en_background` en el punto descrito en el plan de
  3.1) — se descifra, se usa para `POST /rest/user/login` contra el target,
  y el JWT resultante (no la contraseña) es lo que se pasa al replacer de
  ZAP.
- **Ningún endpoint de la API devuelve el valor descifrado.** El equivalente
  a `AssetOut` para credenciales (si existe un `GET` de estado) debe exponer
  como máximo `{"tiene_credenciales": true, "username_parcial": "adm***",
  "rotated_at": ...}` — mismo principio que ya usa `UserContext` al no
  incluir `hashed_password`.
- A nivel de rol: solo `owner`/`admin` del tenant puede crear o rotar
  credenciales de un asset (mismo criterio de permisos que ya protege
  operaciones sensibles de asset, a verificar contra el decorator/dependency
  de roles existente al implementar — fuera de alcance de esta decisión
  definir el nombre exacto del rol).

### Qué pasa en un dump de la DB
Un dump completo (SQLite file o `pg_dump`) expone únicamente los tokens
Fernet cifrados — sin `VIGIA_CREDENTIALS_KEY` (que vive fuera de la DB, en el
entorno del proceso) son inútiles. Esto es exactamente el mismo modelo de
amenaza que ya asume el proyecto para `JWT_SECRET`: comprometer solo la DB no
alcanza, hace falta también comprometer el entorno de ejecución del backend.

### Rotación y borrado
- **Rotación**: `PATCH /assets/{id}/credentials` (nuevo endpoint, no
  implementado en esta sesión) sobrescribe `username_encriptado` /
  `password_encriptado` y actualiza `rotated_at` — no se guarda historial de
  valores anteriores (un secreto de tercero rotado no debería dejar rastro
  del valor viejo en la DB).
- **Borrado**: `DELETE /assets/{id}/credentials` explícito, y borrado en
  cascada automático si se borra el asset o el tenant (`ON DELETE CASCADE`).
  No hay soft-delete para esta tabla — un secreto de tercero que el cliente
  pide borrar debe desaparecer de la fila físicamente, no marcarse inactivo.
- **Re-cifrado ante rotación de `VIGIA_CREDENTIALS_KEY`**: si la master key
  del entorno se rota (evento operativo, no de producto), todas las filas
  existentes quedan indescifrables — requiere una migración explícita
  (descifrar con la key vieja, re-cifrar con la nueva) antes de rotar en
  producción. Documentar esto como runbook cuando se implemente, no es parte
  del alcance de este documento de decisión.

## Qué NO decide este documento (fuera de alcance)
- El nombre/forma exacta del endpoint CRUD de credenciales.
- El mecanismo de permisos por rol exacto (asume que ya existe algo
  reusable, a confirmar al implementar).
- Rotación automática programada (ej. cada N días) — solo rotación manual
  bajo demanda por ahora.
