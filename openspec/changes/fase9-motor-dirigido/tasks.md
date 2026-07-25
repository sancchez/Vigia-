# Tasks: Fase 9 — Motor de detección dirigido

Método: **eval-driven**. Antes y después de CADA grupo, correr:

```bash
py -3 eval/run_eval.py --findings <archivo> --json
```

y anotar el número en `eval/failure_log.md`. Si un chequeo no mueve `recall`,
se revierte. El GT (`ground_truth.yaml`) se consulta SOLO por `run_eval`, nunca
mientras se escribe un chequeo (ver Grupo 4, salvaguarda anti-trampa).

## Grupo 0 — Plomería de matching (prerequisito, empezar acá)

Sin esto, ningún hallazgo puede matchear aunque sea correcto. Mueve el recall
0.0 → 0.0 sobre la salida cruda actual (medido), pero desbloquea que TODO lo
demás pueda puntuar. Ver proposal §6.

- [ ] 0.1 Reproducir la base: `py -3 eval/run_eval.py --findings eval/baseline_real/juiceshop_autenticado_2026-07-25_findings_raw.json --json` → debe dar `recall 0.0`, `fp 11`, `fn 11`. Confirmar el harness antes de cambiar nada.
- [ ] 0.2 **Normalización de ubicación**: derivar un path relativo (`urlparse(url).path`) para el matching, en vez de la URL absoluta (`http://host…:3007/...`). Decidir dónde vive: (a) capa de adaptación al emitir el JSON de findings para el eval, o (b) promover `raw_json.url` → path en el punto de persistencia. Recomendado: (a) primero (más barato, sin migración), evaluar (b) solo si el panel también lo necesita. Verificado offline: `locations_match('/rest/products/search', <url_absoluta>)` = False; con path = True.
- [ ] 0.3 **Normalización de tipo**: mapa nombre-de-alerta-ZAP → tipo canónico del GT (`'SQL Injection'` → `sql_injection`, etc.). Función pura, mismo patrón determinista que `_RUIDO_ZAP` en `tools/clasificacion.py`. Solo los nombres verificados contra las corridas reales de `eval/baseline_real/`.
- [ ] 0.4 Medir: re-correr 0.1 con la normalización aplicada. Documentar que sigue en `recall 0.0` (el único SQLi de ZAP está en `/search`, cuya (tipo, ubicación) no corresponde a ninguna entrada del GT) — es esperado, no una regresión.

## Grupo 1 — Andamiaje del tercer motor

- [ ] 1.1 Crear el paquete `tools/chequeos_dirigidos/` con el registro pluggable (decorador `@chequeo` + lista) y el dataclass `ContextoChequeo` (target_url, tokens de sesión, cliente HTTP, endpoints descubiertos por el crawl de ZAP) y `HallazgoDirigido` (shape normalizado `{tipo, severidad, endpoint, confirmado, raw_json}`).
- [ ] 1.2 Enganchar el "Motor 3" en `_correr_escaneo_activo_en_background` (`api/main.py`), después de Nuclei y antes de `estado_final`, con try/except **por chequeo** (un chequeo que revienta no hunde el escaneo). Cada hallazgo lleva `_motor="dirigido"` y `_check_id` en `raw_json`.
- [ ] 1.3 Respetar el constraint de `_combinar_grupo_hallazgos` (`api/main.py:2453`): los chequeos emiten a lo sumo UN hallazgo por (clase, ubicación canónica); varias URLs van como evidencia en `raw_json`, no como filas. Ubicación como path relativo, tipo canónico (nacen esquivando el Grupo 0).
- [ ] 1.4 Verificar que dedup (Grupo 1 Fase 8), ciclo de vida/drift (Fase 2a), clasificación, SLA y aislamiento por tenant siguen intactos con hallazgos dirigidos entrando por `_registrar_findings_con_ciclo_de_vida`.

## Grupo 2 — Chequeos baratos de una sesión (el camino al 0.5)

Cada chequeo se mide individualmente; se conserva solo si mueve `recall`.

- [ ] 2.1 **VULN-001 sql_injection** — payload `' OR 1=1--` contra cada endpoint de login descubierto. Confirmación: 200 + token de sesión devuelto. (ancla SQLi)
- [ ] 2.2 **VULN-007 sensitive_data_exposure** — GET a wordlist genérica de backups/directorios (`.bak`, `.git/`, `package.json.bak`, etc.). Confirmación: 200 + contenido sensible.
- [ ] 2.3 **VULN-008 path_traversal** — secuencias `../` genéricas contra el endpoint de descarga descubierto. Confirmación: se sirve un archivo fuera del directorio previsto.
- [ ] 2.4 **VULN-011 sensitive_data_exposure (rate-limiting)** — N intentos de login consecutivos. Confirmación: ausencia de 429/lockout.
- [ ] 2.5 **VULN-006 jwt_vuln** — análisis offline del JWT que el escaneo ya posee: `alg=none`, confusión de algoritmo, clave débil crackeable.
- [ ] 2.6 **VULN-003 xss_stored** — POST de reseña con payload, GET de vuelta, confirmar que vuelve sin escapar. Una sola sesión autenticada, sin harness de dos usuarios. (ancla XSS)
- [ ] 2.7 Medir: con 2.1–2.6, `recall` debe llegar a **≥6/11 = 0.545**, con SQLi (VULN-001) y XSS (VULN-003) entre los TP. Anotar en `eval/failure_log.md`.
- [ ] 2.8 **VULN-002 xss_reflected** (seguro) — implementar solo si algún chequeo de 2.1–2.6 falla en vivo. Ojo: el reflected de Juice Shop es DOM-based (Angular), frágil de confirmar en caja negra. Alternativa de seguro: VULN-005 (authz del API admin como usuario normal).

## Grupo 3 — Harness de dos usuarios (stretch, sube recall por encima de 0.5)

NO está en el camino crítico al 0.5. Solo si se quiere cobertura IDOR.

- [ ] 3.1 Relajar `UNIQUE(asset_id)` → `UNIQUE(asset_id, rol_prueba)` en `asset_credentials` (`rol_prueba ∈ {'principal','secundario'}`), compatible hacia atrás. Reusa el modelo de `decision-credenciales.md` sin reabrirlo.
- [ ] 3.2 Helper `_autenticar_usuario_prueba(target, login_url, user, pass) -> jwt`, verificado en vivo contra `POST /rest/user/login`. Poblar `token_a` (y `token_b` si hay `secundario`) en el `ContextoChequeo`.
- [ ] 3.3 Un chequeo con `requiere_dos_usuarios=True` sin `secundario` cargado se SALTA con motivo explícito ("requiere segundo usuario de prueba"), nunca con falso negativo silencioso.
- [ ] 3.4 **VULN-004 broken_access_control (IDOR cesta)** — crear recurso con A, leerlo con B. VULN-009 (IDOR PATCH reseña) análogo. Medir el recall incremental.

## Grupo 4 — Salvaguarda anti-trampa y generalización (obligatorio)

- [ ] 4.1 Test de CI que hace grep de las 11 `location` del `ground_truth.yaml` (y rutas Juice-Shop-específicas como `/rest/basket`, `/#/administration`) sobre `tools/chequeos_dirigidos/` y **falla** si aparece alguna. Excepción documentada: wordlists OWASP genéricas (`.bak`, `../etc/passwd`) están permitidas.
- [ ] 4.2 Correr los mismos chequeos, sin cambios de código, contra DVWA (`eval/ground_truth_dvwa.yaml`). Aceptación: detectar SQLi (DVWA-001/002) y una clase de archivo/path (DVWA-005). Un chequeo que puntúa 0 en su clase en DVWA se presume sobreajustado y se rechaza.
- [ ] 4.3 (Opcional) Repetir contra WebGoat (`eval/ground_truth_webgoat.yaml`) para las clases que apliquen.

## Grupo 5 — Cierre

- [ ] 5.1 `recall >= 0.5` confirmado en Juice Shop y anotado en `eval/failure_log.md`, con SQLi y XSS reales entre los TP.
- [ ] 5.2 Gate de generalización (4.2) verde: recall no nulo en DVWA con el mismo código.
- [ ] 5.3 Actualizar `eval/baseline.json` con la nueva línea base para que el gate de regresión (Fase 7) proteja el avance.
- [ ] 5.4 Verificar que la suite existente sigue verde (`py -3 -m pytest tests/ -q`) — no como gate de calidad, sino para confirmar que no se rompió nada.
- [ ] 5.5 Commit + `git push privado main:producto`. **No pushear a `origin`** (público).

## Orquestación sugerida de agentes

El Grupo 0 (plomería) y el Grupo 1 (andamiaje) son prerequisitos secuenciales.
El Grupo 2 se puede paralelizar por chequeo **solo si cada chequeo mide su
propio delta de recall aislado** — pero el número final es acumulativo, así que
conviene medir en serie al integrar. Los Grupos 3 (harness) y 4 (anti-trampa)
son independientes entre sí y pueden ir en paralelo una vez que el Grupo 2
alcanza el 0.5.

Modelo: la arquitectura del tercer motor y el harness de dos usuarios (Grupos
1 y 3) justifican Opus; los chequeos individuales (Grupo 2) son investigación
técnica acotada con un número objetivo — Sonnet alcanza.
