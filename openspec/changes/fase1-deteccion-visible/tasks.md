# Tasks: Fase 1 — Bucle de Detección visible

## Phase 0: Decisiones previas (bloquean Fase 3 y Fase 4)

- [x] 0.1 Leer `tools/scan.py` (funciones `run_nuclei`, `run_semgrep`, `run_trivy_image`, `run_grype`) y confirmar el shape real de cada `raw` — ¿usan las mismas claves `description`/`solution`/`reference`/`cweid` que ZAP, o shape propio por herramienta? Documentar el resultado en `design.md` (actualizar la sección "Open Questions").
- [x] 0.2 Decisión resultante de 0.1: si los shapes difieren, diseñar dispatch por `herramienta` en `_detalle_desde_raw_json` (mismo patrón que `normalize_severity` en `tools/_shared.py`); si se decide acotar el drill-down a hallazgos ZAP en esta fase, dejarlo explícito como límite conocido (no silencioso) en `design.md` y en el docstring de `_detalle_desde_raw_json`.
- [x] 0.3 Confirmar con el usuario si el chip de verificación reusa `.chip.info`/`.chip.verificado` + nueva `.chip.sin-poc`, o si prefiere paleta distinta, antes de tocar `frontend/src/index.css` (Fase 4).

## Phase 1: Backend — verificación pública

- [x] 1.1 En `agents/verificacion.py`, agregar `verificar_un_hallazgo(hallazgo: dict) -> dict` como wrapper público de `_verificar_un_hallazgo`; `node()` sigue llamando a la privada, sin cambios de comportamiento.
- [x] 1.2 Test unitario en `tests/test_verificacion_escaneo_activo.py`: `verificar_un_hallazgo(alerta_con_cve_confirmable)` devuelve `estado_verificacion == "confirmado_via_exploitdb"`.
- [x] 1.3 Mismo archivo: test con alerta sin CVE en su texto → `estado_verificacion == "no_verificable_sin_cve"`.
- [x] 1.4 Mismo archivo: test con CVE real pero sin entrada en Exploit-DB → `estado_verificacion == "no_confirmado_cve_sin_poc_publica"`.

## Phase 2: Backend — verificación en escaneo activo

- [x] 2.1 En `api/main.py::_correr_escaneo_activo_en_background`, antes del `INSERT INTO findings` (línea ~1189), llamar `hallazgo_verificado = verificar_un_hallazgo(hallazgo)`.
- [x] 2.2 Usar `int(bool(hallazgo_verificado["confirmado"]))` en vez del `0` fijo, y `json.dumps(hallazgo_verificado)` en vez de `json.dumps(hallazgo)` para `raw_json`.
- [x] 2.3 Test de integración en `tests/test_verificacion_escaneo_activo.py`: mockear `run_checkpointed_active_scan` devolviendo una alerta con CVE confirmable y otra sin CVE; assert que la fila `findings` resultante tiene `confirmado` correcto por fila y `raw_json` contiene `estado_verificacion`.

## Phase 3: Backend — `GET /findings/{id}`

- [x] 3.1 En `api/main.py`, agregar modelo `FindingDetailOut` (campos: `id, scan_id, tipo, severidad, endpoint, confirmado, created_at, descripcion, solucion, referencia, cwe, estado_verificacion, cves_detectados, evidencia_exploitdb` con defaults según `design.md`).
- [x] 3.2 Implementar `_detalle_desde_raw_json(raw_json_str: str) -> dict` con `try/except` sobre `json.loads` y `.get(...)` con defaults; aplicar la decisión de la tarea 0.2 (mapeo directo o dispatch por herramienta).
- [x] 3.3 Agregar `GET /findings/{finding_id}` con `SELECT * FROM findings WHERE id = ? AND tenant_id = ?` (scoping igual a `GET /scans/{scan_id}`); 404 si no hay fila.
- [x] 3.4 Test en `tests/test_finding_detail.py`: 200 con payload curado para finding del propio tenant.
- [x] 3.5 Mismo archivo: 404 para finding de otro tenant.
- [x] 3.6 Mismo archivo: 404 para id inexistente.
- [x] 3.7 Mismo archivo: 401 sin JWT válido.
- [x] 3.8 Mismo archivo: `raw_json` viejo (sin campos de verificación) → 200 con defaults vacíos, no error.
- [x] 3.9 Mismo archivo: finding con `evidencia_exploitdb` no vacío → la respuesta incluye la lista completa con `edb_id` y `descripcion`.

## Phase 4: Frontend

- [x] 4.1 En `frontend/src/api.ts`, agregar tipo `FindingDetail` (extiende `Finding`) y método `api.getFinding(id: string)`.
- [x] 4.2 Crear `frontend/src/components/FindingDetailPanel.tsx`: props `{ findingId: string | null; onClose: () => void }`, fetch on-demand vía `useEffect` cuando `findingId` cambia, overlay + tarjeta con descripción/solución/endpoint/severidad/chip de verificación/evidencia Exploit-DB.
- [x] 4.3 En `frontend/src/pages/Dashboard.tsx`, agregar `useState<string | null>` para el finding abierto; `onClick` en `<tr>` de la tabla general de hallazgos (línea ~524).
- [x] 4.4 Mismo archivo: `onClick` en `<tr>` de la tabla de detalle de scan (línea ~674).
- [x] 4.5 Renderizar `<FindingDetailPanel>` condicionalmente en `Dashboard.tsx` según el estado del punto 4.3.
- [x] 4.6 Reemplazar el chip binario actual (línea ~532-535) por el chip explicado según `estado_verificacion`, usando la paleta confirmada en la tarea 0.3.
- [x] 4.7 Agregar estilos nuevos en `frontend/src/index.css` (overlay, tarjeta del panel, y clase de chip nueva si 0.3 la requiere).

## Phase 5: Verificación end-to-end

- [x] 5.1 Correr `pytest tests/` completo — suite debe quedar verde.
- [x] 5.2 Correr un escaneo activo real (o con datos de prueba/mock) y confirmar en la DB que al menos un finding tiene `estado_verificacion` distinto de vacío.
- [x] 5.3 Verificación manual en el Dashboard: click en fila de ambas tablas abre el panel con descripción, solución, chip explicado y evidencia Exploit-DB cuando aplica.
- [x] 5.4 Confirmar que `GET /findings` y `GET /scans/{id}` (contratos existentes) no cambiaron de forma — no rompieron consumidores actuales del frontend.
