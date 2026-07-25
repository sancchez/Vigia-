# Fase 9 — Motor de detección dirigido

> **Leer esto primero en cualquier sesión nueva.** Continúa la Fase 8, que se
> cerró con resultado negativo (`ff19d7a`) y está commiteada. La Fase 8 probó
> —midiendo, no suponiendo— que `recall >= 0.5` NO se alcanza tuneando
> ZAP+Nuclei. Esta fase construye la única palanca que queda: chequeos
> dirigidos por clase de vulnerabilidad. Sigue siendo trabajo **eval-driven**,
> no test-driven: el gate es `eval/run_eval.py`, no la suite verde.

## 1. Qué dejó medido la Fase 8 (evidencia, no supuesto)

Corrida real autenticada del 2026-07-25 contra OWASP Juice Shop v20.1.1
(JWT `admin@juice-sh.op`, `_correr_escaneo_activo_en_background` directo, DB
aislada, ~25 min ZAP + Nuclei). Evidencia:
`eval/baseline_real/juiceshop_autenticado_2026-07-25_findings_raw.json` (11
filas) y `..._findings_zap.json` / `..._findings_nuclei.json`.

```
TOTAL     {"tp":0,"fp":11,"fn":11,"precision":0.0,"recall":0.0,"f1":0.0,"fp_rate":1.0}
solo-ZAP  idéntico
solo-Nuclei {"tp":0,"fp":0,"fn":11}
```

Lo que se confirmó en vivo y **no** hay que rehacer:

- La autenticación SÍ amplió el crawling: ZAP pasó de 594 a 760 alertas
  crudas. No fue el problema.
- La deduplicación del Grupo 1 funciona en producción: 760 → 11 filas
  (`_deduplicar_hallazgos`, `api/main.py`).
- El cableado de Nuclei quedó correcto (aunque aportó 0 hallazgos).

Lo que quedó como techo duro:

- De los **11 hallazgos reales**, **solo 1 es una vulnerabilidad explotable**:
  `SQL Injection` en `/rest/products/search?q=` (payload `q=%27%28`, genuina,
  detectada por ZAP). Los otros 10 son ruido de configuración/informativo
  (CORS, CSP, headers faltantes, robots/sitemap, socket.io, timestamps, UA
  fuzzer).
- Las otras 10 vulns del ground truth (XSS stored en reviews, IDOR de
  cesta, panel admin, JWT, exposición `/ftp`, path traversal, reset-password,
  rate-limiting) **no aparecen en absoluto en la salida cruda**. Son lógica de
  negocio y control de acceso: un DAST genérico no las alcanza sin casos de
  prueba dirigidos.
- Incluso ese único SQLi no cruza el eval: está en `/rest/products/search`,
  no en `/rest/user/login` (VULN-001), y su `tipo` es `sql_injection` mientras
  el GT en `/search` es `xss_reflected` (VULN-002). Ninguna combinación
  (tipo, ubicación) matchea.

**Conclusión ya aceptada por el usuario: el 0.5 exige detección dirigida, no
más crawling.**

## 2. Distribución del ground truth (extraída, es el mapa de trabajo)

```
3x broken_access_control  VULN-004 /rest/basket/{id} · VULN-005 /#/administration · VULN-009 /rest/products/reviews (PATCH)
2x sensitive_data_exposure VULN-007 /ftp/ · VULN-011 /rest/user/login (rate-limiting)
1x sql_injection          VULN-001 /rest/user/login (' OR 1=1--)
1x xss_reflected          VULN-002 /rest/products/search (param q)
1x xss_stored             VULN-003 /rest/products/reviews
1x jwt_vuln               VULN-006 /rest/user/login
1x path_traversal         VULN-008 /ftp/:file
1x broken_authentication  VULN-010 /rest/user/reset-password
```

VULN-004, 005, 009 y 010 requieren **dos usuarios distintos** o un flujo con
estado para probarse (IDOR / authz / recuperación de cuenta).

## 3. Arquitectura: un tercer motor pluggable

Hoy `_correr_escaneo_activo_en_background` (`api/main.py:3003`) tiene dos
bloques explícitos —"Motor 1: ZAP" y "Motor 2: Nuclei"— cada uno envuelto en
su propio `try/except`, que normalizan sus hallazgos al mismo shape
(`{tipo, severidad, endpoint, confirmado, raw_json}`) y los acumulan en
`hallazgos_normalizados`, que al final pasa una sola vez por
`_registrar_findings_con_ciclo_de_vida`.

La Fase 9 agrega un **"Motor 3: chequeos dirigidos"** con exactamente el mismo
contrato:

- **Registro pluggable.** Un módulo nuevo `tools/chequeos_dirigidos/`
  (paquete, no inline en `main.py`) con un registro (lista/decorador
  `@chequeo`). Un chequeo nuevo = una función que recibe un `ContextoChequeo`
  (target_url, tokens de sesión, cliente HTTP, endpoints descubiertos por el
  crawl de ZAP) y devuelve `list[HallazgoDirigido]` ya en el shape
  normalizado. Registrar un chequeo NO toca el orquestador.
- **Enganche.** El bloque Motor 3 corre después de Nuclei y antes del cómputo
  de `estado_final`, con la misma disciplina try/except **por chequeo** (un
  chequeo que revienta nunca hunde el escaneo ni a los otros chequeos).
- **Atribución.** Cada hallazgo lleva `_motor="dirigido"` y `_check_id` en
  `raw_json`, igual que hoy se hace con `_motor="zap"/"nuclei"`.
- **Sin romper nada aguas abajo.** Los hallazgos dirigidos entran por
  `_registrar_findings_con_ciclo_de_vida`, así que dedup (Grupo 1), ciclo de
  vida/drift (Fase 2a), clasificación (`tools/clasificacion.py`), SLA y
  aislamiento por tenant se aplican **sin cambios**.
- **Los chequeos nacen canónicos.** Emiten directamente el `tipo` del GT
  (`sql_injection`, `xss_stored`, …) y un `endpoint` como **path relativo**
  (`/rest/user/login`), no la URL absoluta. Así esquivan por construcción los
  dos gaps de plomería del Grupo 0 (§6). El Grupo 0 sigue siendo necesario
  para que los hallazgos de ZAP/Nuclei sean matcheables, pero los dirigidos no
  dependen de él.

**Gotcha real encontrado leyendo el código (constraint de integración).**
`_combinar_grupo_hallazgos` (`api/main.py:2453`), cuando agrupa 2+ hallazgos
del mismo `(tipo, param)`, **reescribe `endpoint` con el parámetro o el
`scheme://netloc`** — destruiría el path limpio que un chequeo dirigido
necesita para matchear en el eval. Un hallazgo dirigido único pasa intacto (la
rama `len(miembros)==1` lo devuelve tal cual), pero si un chequeo emite varios
hallazgos del mismo tipo, colapsan y se pierde la ubicación. Regla de diseño
para los chequeos: **emitir a lo sumo un hallazgo por (clase, ubicación
canónica)**; si se necesita reportar varias URLs, van como evidencia dentro de
`raw_json`, no como filas separadas.

## 4. El harness de dos usuarios (el problema estructural nuevo)

Hoy el escaneo asume UNA sesión (`payload.bearer_token`, un solo JWT). El IDOR
exige usuario A y usuario B: crear/leer un recurso con A e intentar accederlo
con B; una fuga = hallazgo.

Modelo propuesto, **construido sobre `decision-credenciales.md`** (Fase 8,
tarea 3.2) sin reabrir esa decisión:

- Esa decisión ya definió la tabla `asset_credentials` (Fernet +
  `VIGIA_CREDENTIALS_KEY`, texto plano que nunca sale del thread de escaneo,
  aislamiento por tenant, `UNIQUE(asset_id)`). El **único delta** de la Fase 9
  es admitir **dos filas por asset**: relajar `UNIQUE(asset_id)` a
  `UNIQUE(asset_id, rol_prueba)` con `rol_prueba ∈ {'principal','secundario'}`.
  `principal` es el caso de una sola credencial de hoy (compatible hacia
  atrás); `secundario` solo se carga si el cliente quiere cobertura IDOR.
- En tiempo de escaneo, `_correr_escaneo_activo_en_background` descifra las
  filas presentes y hace login contra `login_url` del asset para obtener
  `token_a` (y `token_b` si existe `secundario`), vía un helper
  `_autenticar_usuario_prueba(target, login_url, user, pass) -> jwt`
  verificado en vivo contra `POST /rest/user/login`.
- El `ContextoChequeo` lleva ambos tokens. Un chequeo declara
  `requiere_dos_usuarios=True`; si el asset no tiene `secundario`, ese chequeo
  se **salta con motivo explícito** ("requiere segundo usuario de prueba"), no
  con un falso negativo silencioso.
- Reutiliza el mismo modelo de amenaza de `decision-credenciales.md` (comprometer
  la DB no basta sin `VIGIA_CREDENTIALS_KEY`). Cero secreto nuevo, cero librería
  nueva más allá de la ya decidida (`cryptography`).

**Recomendación de secuenciación (ver §5): el harness de dos usuarios NO está
en el camino crítico al 0.5.** Se diseña acá porque es la novedad estructural
real y habilita la cobertura completa, pero el criterio de aceptación se
alcanza antes con chequeos de una sola sesión.

## 5. Priorización por costo/beneficio

Ordenado por (impacto en recall) / (esfuerzo). "Barato" = una sola sesión,
señal de confirmación no ambigua.

**Baratos (una sesión, señal limpia):**

| VULN | Clase | Chequeo | Señal de confirmación |
|---|---|---|---|
| VULN-001 | sql_injection | payload `' OR 1=1--` al endpoint de login descubierto | 200 + token de sesión devuelto = bypass |
| VULN-007 | sensitive_data_exposure | GET a wordlist genérica de backups/dirs | 200 + contenido sensible |
| VULN-008 | path_traversal | secuencias `../` en el endpoint de descarga | se sirve un archivo fuera del directorio |
| VULN-011 | sensitive_data_exposure | N intentos de login seguidos | sin 429/lockout = sin rate-limiting |
| VULN-006 | jwt_vuln | análisis offline del JWT que ya tenemos | `alg=none`/confusión/clave débil crackeable |

**Medios (una sesión, con escritura+lectura o superficie a descubrir):**

| VULN | Clase | Chequeo | Costo |
|---|---|---|---|
| VULN-003 | xss_stored | POST reseña con payload, GET de vuelta, ver si vuelve sin escapar | escritura+lectura autenticada, una sola sesión |
| VULN-002 | xss_reflected | payload en el param reflejado, ver reflexión | **frágil**: en Juice Shop el reflected es DOM-based (Angular), puede no confirmarse en la respuesta cruda |
| VULN-005 | broken_access_control | golpear el API REST detrás del panel admin como usuario normal | 200 donde debería 401/403 |

**Caros (dos usuarios / flujo con estado — dependen del harness §4):**

VULN-004 (IDOR cesta), VULN-009 (IDOR PATCH reseña), VULN-010
(reset-password por preguntas de seguridad).

### Subconjunto que llega a 6/11 con el mínimo esfuerzo

**VULN-001, VULN-007, VULN-008, VULN-011, VULN-006, VULN-003.**

- 6/11 = **recall 0.545 ≥ 0.5**.
- Incluye **un SQLi real** (VULN-001) y **un XSS real** (VULN-003 stored),
  cumpliendo el criterio explícito de la fase.
- **Cero harness de dos usuarios.** Los cinco baratos son una sesión; VULN-003
  es escritura+lectura con un solo usuario. El ancla de XSS es la *stored* y no
  la *reflected* precisamente porque la reflected de Juice Shop es DOM-based y
  frágil de confirmar en caja negra.
- VULN-002 se implementa como **séptimo chequeo de seguro** (barato-ish): si
  cualquiera de los seis anteriores falla en vivo, VULN-002 o VULN-005 lo
  cubren sin tocar el harness.

El harness de dos usuarios (VULN-004/005/009/010) es un **stretch** que sube el
recall por encima de 0.5, no un prerequisito de él.

## 6. El gap de plomería (Grupo 0, prerequisito de todo)

La fila persistida hoy hace imposible que **ningún** hallazgo matchee, aunque
sea correcto. Dos defectos, medidos:

1. **Ubicación absoluta vs. path.** `endpoint` guarda la URL absoluta
   (`http://host.docker.internal:3007/rest/products/search?q=...`).
   `run_eval.normalize_location` no quita esquema/host, así que el hallazgo
   normaliza a `http://host…` y el GT a `/rest/products/search` — comparten 0
   segmentos desde el inicio → no matchean. Verificado:
   `locations_match('/rest/products/search', <url_absoluta>)` = **False**;
   con el path relativo = **True**.
2. **Nombre de alerta vs. tipo canónico.** ZAP emite `'SQL Injection'`; el GT
   usa `sql_injection`. `run_eval` exige `type` exacto. Sin un mapa
   nombre-ZAP → tipo-canónico, ningún hallazgo de ZAP puede cruzar.

Grupo 0 = una capa de normalización pura y testeable offline (mapa de nombres
+ `urlparse().path`), aplicada donde se da forma al hallazgo para el eval y
para la persistencia. Mismo patrón determinista que `_RUIDO_ZAP` en
`tools/clasificacion.py`.

**Honestidad sobre su impacto medido:** Grupo 0 por sí solo mueve el recall de
**0.0 → 0.0** sobre la salida cruda actual (el único hallazgo explotable de ZAP
es un SQLi en `/search`, cuya (tipo, ubicación) no corresponde a ninguna
entrada del GT). Su valor no es subir el número hoy, sino **desbloquear que los
hallazgos dirigidos del Grupo 1+ puedan puntuar**. Es prerequisito, no palanca.

## 7. Riesgo de trampa (crítico y obligatorio)

Chequeos escritos mirando el ground truth degeneran en memorización: pasan el
eval sin detectar nada en un cliente real. Salvaguardas **accionables y
verificables**:

1. **Chequeos contra CLASES OWASP, nunca contra rutas del GT.** Ninguna cadena
   de las 11 `location` del `ground_truth.yaml` (ni rutas Juice-Shop-específicas
   como `/rest/basket`, `/#/administration`) puede aparecer en el código de
   `tools/chequeos_dirigidos/`. **Se hace cumplir con un test de CI que hace
   grep** de esas cadenas sobre el paquete y falla si aparece alguna.
2. **Targeting por descubrimiento, no hardcode.** Un chequeo declara qué tipo
   de endpoint busca (p. ej. "formularios de login") y los recibe del árbol
   crawleado por ZAP, no de `/rest/user/login` escrito a mano. El chequeo de
   SQLi corre contra *cualquier* endpoint de autenticación descubierto.
   - **Excepción explícita y acotada:** los chequeos de exposición de backups
     y path traversal usan wordlists **genéricas** (`.bak`, `.git/`,
     `package.json.bak`, `../etc/passwd`) aplicadas a *cualquier* host. Se
     permiten porque no derivan del GT y generalizan; la prohibición es sobre
     las rutas específicas del target, no sobre listas OWASP genéricas.
3. **Validación contra un segundo target, sin tocar el código.** Los mismos
   chequeos corren contra DVWA (`eval/ground_truth_dvwa.yaml`) y/o WebGoat
   (`eval/ground_truth_webgoat.yaml`). Un chequeo que solo dispara en Juice
   Shop es memorización. Concreto: el chequeo de SQLi debe detectar DVWA-001
   (`/vulnerabilities/sqli/`) y el de path traversal / file inclusion debe
   detectar DVWA-005 (`/vulnerabilities/fi/`), **con el mismo código**.
4. **Disciplina de desarrollo a ciegas.** Los chequeos se escriben desde la
   descripción de la clase en la OWASP Testing Guide; el `ground_truth.yaml`
   se consulta ÚNICAMENTE por `run_eval` al puntuar, nunca abierto mientras se
   escribe un chequeo. El grep de la salvaguarda 1 lo fuerza mecánicamente.

## 8. Criterio de aceptación

- **`recall >= 0.5`** (≥6/11) contra `ground_truth.yaml` de Juice Shop, medido
  con `eval/run_eval.py`, con **al menos un SQLi y un XSS reales** entre los TP.
- **Gate de generalización:** el mismo código de chequeos, corrido contra DVWA
  sin cambios, detecta al menos la clase SQLi (DVWA-001/002) y al menos una
  clase de archivo/path (DVWA-005) — recall no nulo en un segundo target con
  cero especialización. Un chequeo que pasa Juice Shop pero puntúa 0 en su
  propia clase en DVWA se presume sobreajustado y se rechaza.
- **`fp_rate`** solo se despega de 1.0 cuando `tp > 0` (ver `failure_log.md`,
  nota del Grupo 1): son los chequeos dirigidos los que finalmente hacen
  `tp > 0`. Cada chequeo dispara solo con señal confirmada (token devuelto,
  archivo 200 con contenido sensible, payload reflejado sin escapar), así que
  cada TP dirigido sube la precisión.
- Método **eval-driven**: medir antes/después de CADA grupo y anotar en
  `eval/failure_log.md`. Un chequeo que no mueve `recall` se descarta.

## 9. Alcance explícito de lo que NO entra

- Nada de producto nuevo (tablero, notificaciones, billing, UI, Fase 6b/Wompi).
- No tocar `vigia_core_private/`.
- No agregar tests unitarios salvo el grep anti-trampa (§7.1) y los que
  protejan una regresión medida.
- No reabrir `decision-credenciales.md`: la Fase 9 solo agrega la segunda fila
  de credencial, no rediseña el cifrado.
- No perseguir recall 1.0. VULN-010 (reset-password) es frágil y de bajo
  retorno; queda fuera del compromiso de esta fase.
- `main` se pushea a `privado/producto`, NO a `origin` (público). Ver
  `docs/open-core.md`.
