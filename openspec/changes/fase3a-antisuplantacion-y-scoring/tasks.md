# Tasks: Fase 3a — Anti-suplantación bajo demanda + scoring de dominios clon

## Phase 1: Tools — probes de infraestructura y screenshot (público)

- [ ] 1.1 En `tools/antisuplantacion.py`, agregar dataclass `InfraScore` (`dns_a`, `mx`, `ssl`, `http`, `ssl_not_before`, `score`).
- [ ] 1.2 Agregar `puntuar_infra_lookalike(dominio, timeout=10)`: A-record (socket), MX (dnspython, graceful si falta), SSL handshake + capturar `NotBefore`, HTTP(S) GET vivo; `score = sum` de los 4 booleanos.
- [ ] 1.3 Agregar `capturar_screenshot(url, out_dir, timeout=20)`: Playwright chromium headless; devuelve path o `None` con `ToolNotInstalledError`/ImportError graceful (patrón de `check_certthreat`).
- [ ] 1.4 Test `tests/test_antisuplantacion_scoring.py`: cada combinación de señales → 0..4; MX resolver ausente cuenta `false` sin romper; `capturar_screenshot` sin navegador → `None`.

## Phase 2: API — endpoint de disparo y enriquecimiento (público)

- [ ] 2.1 En `api/main.py`, agregar `_enriquecer_dominios_clon(señales, out_dir)`: por cada `dominio_variante` llama `puntuar_infra_lookalike` + `capturar_screenshot`, inyecta `scoring`/`score`/`ssl_not_before`/`screenshot` en el `raw_json` de la señal.
- [ ] 2.2 Agregar modelos `DominioClonOut`, `AntisuplantacionRunOut`, `AntisuplantacionKpiOut` (shapes exactos de design.md).
- [ ] 2.3 Implementar `POST /antisuplantacion`: SELECT asset por `id`+`tenant_id` (404 ajeno, 403 si no `verificado`/exento); INSERT `scans` con `asset_id`; correr `agents.antisuplantacion.node`; enriquecer; `_extraer_findings_antisuplantacion` + `_registrar_findings_con_ciclo_de_vida`. Correr en background (patrón `_correr_escaneo_activo_en_background`), devolver `scan_id`.
- [ ] 2.4 Test `tests/test_antisuplantacion_endpoint.py` (parte 1): 403 no verificado, 404 cross-tenant, 200 crea findings con `score` en `raw_json` (nodo y probes mockeados).

## Phase 3: API — lectura, KPIs y screenshot (público)

- [ ] 3.1 `GET /antisuplantacion/dominios`: SELECT findings `tipo IN (dominio_variante, perfil_red_social)` WHERE `tenant_id`; parsear `raw_json` a `DominioClonOut` (score, scoring, screenshot URL, takedown=`analisis_claude`).
- [ ] 3.2 `GET /antisuplantacion/dominios/{finding_id}/screenshot`: servir el archivo tenant-scoped; 404 si ajeno o sin screenshot.
- [ ] 3.3 `GET /antisuplantacion/kpis`: TTTD exacto (primera transición a `cerrado` en `finding_transiciones` − `primera_vez_visto`, en Python vía `_parse_dt_utc_naive`); MTTD best-effort (`primera_vez_visto` − `ssl_not_before` de `raw_json`), excluyendo nulos del promedio.
- [ ] 3.4 Test `tests/test_antisuplantacion_endpoint.py` (parte 2): `GET dominios` scoping por tenant; TTTD ≈ N exacto; MTTD null sin SSL; KPIs vacíos sin clones; screenshot 404 cross-tenant.

## Phase 4: Frontend — panel Protección de marca (público)

- [ ] 4.1 En `frontend/src/api.ts`: tipos `DominioClon`, `AntisuplantacionKpis`; funciones `runAntisuplantacion(assetId)`, `getDominiosClon()`, `getAntisuplantacionKpis()`, `screenshotUrl(findingId)`.
- [ ] 4.2 En `frontend/src/index.css`: clases `.chip.score-bajo` (0-1), `.chip.score-medio` (2-3), `.chip.score-alto` (4), reusando el patrón `.chip.sla-*`.
- [ ] 4.3 Crear `frontend/src/components/BrandProtectionPanel.tsx`: tabla de clones (dominio, badge de score, estado, screenshot thumbnail), lista de perfiles sociales, borrador de takedown copiable (botón copiar), tiles MTTD/TTTD con precisión.
- [ ] 4.4 En `frontend/src/pages/Dashboard.tsx`: botón "Proteger marca" por asset `tipo=dominio` verificado (llama `runAntisuplantacion`, poll del scan), montar `<BrandProtectionPanel/>`, fetch en `cargarTodo()`.

## Phase 5: Verificación end-to-end

- [ ] 5.1 Correr `pytest tests/` completo — suite verde, incluyendo los tests nuevos y sin romper `test_finding_detail.py`/multi-tenant.
- [ ] 5.2 Confirmar degradación: sin `dnspython`/`playwright` instalados, `POST /antisuplantacion` completa y persiste clones (score parcial, screenshot null) sin excepción.
- [ ] 5.3 Confirmar que NO se tocó `vigia_core_private/` ni el `SYSTEM_PROMPT` de `agents/antisuplantacion.py` (open-core: scoring 0-4 público, ver design.md).
