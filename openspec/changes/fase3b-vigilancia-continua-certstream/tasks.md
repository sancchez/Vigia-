# Tasks: Fase 3b+3c — Vigilancia continua (panel CertStream + feed self-hosted)

## Phase 1: Backend — CRUD watched_brands (`api/main.py`)

- [x] 1.1 Agregar modelos `WatchedBrandIn` (`marca: str`) y `WatchedBrandOut` (`id, marca, is_active, created_at`).
- [x] 1.2 `GET /watched-brands`: `WHERE tenant_id = ? ORDER BY created_at DESC` (patrón `listar_findings`).
- [x] 1.3 `POST /watched-brands`: validar `marca` no-vacía tras strip (422 si vacía); dedup marca activa del tenant; `uuid.uuid4()` en Python.
- [x] 1.4 `DELETE /watched-brands/{id}`: soft-delete `is_active=0` del tenant; 404 si es de otro tenant.
- [x] 1.5 Insertar el bloque antes del catch-all SPA (~línea 2432); no tocar rutas existentes.

## Phase 2: Listener — consumir watched_brands (`api/certstream_listener.py`)

- [x] 2.1 En `_build_variant_map()`, tras el bucle de assets, leer `watched_brands` activas por tenant (`SELECT ... WHERE is_active = 1`).
- [x] 2.2 Normalizar marca→dominio candidato: si contiene `.` usar tal cual, si no anexar `.com`; pasar a `generate_domain_variants`.
- [x] 2.3 Envolver cada marca en `try/except` (log + continuar), igual que el bucle de assets; contar/loguear omitidas.
- [x] 2.4 Confirmar que el `refresh` periódico (`_ensure_variant_map`) reconstruye con las marcas nuevas (ya lo hace por diseño).

## Phase 3: Frontend — API + componentes

- [x] 3.1 `frontend/src/api.ts`: tipo `WatchedBrand`; funcs `listWatchedBrands`, `createWatchedBrand(marca)`, `deleteWatchedBrand(id)`.
- [x] 3.2 Crear `VigilanciaPanel.tsx`: recibe `findings`+`members`+`onChanged`; filtra `tipo === "dominio_variante_certstream"`; tabla (base, observado, cuándo, severidad, estado) reusando `.chip`/badge SLA de Fase 2b; estado vacío sin ruido.
- [x] 3.3 En `VigilanciaPanel.tsx`, reusar `api.patchFinding` para cambiar estado/owner (mismo patrón revert que `RemediationBoard`).
- [x] 3.4 Crear `WatchedBrandsConfig.tsx`: input+submit (crear), lista con baja; error visible; refresca vía `onChanged`.
- [x] 3.5 `Dashboard.tsx`: importar+montar ambos; agregar `listWatchedBrands()` a `cargarTodo()` y pasar props.
- [x] 3.6 `index.css`: solo si hace falta clase nueva (preferir reuso). — No hizo falta: se reusaron `.chip`/`.chip.info`/`.chip.sla-*` existentes.

## Phase 4: Infra — feed self-hosted (3c)

- [x] 4.1 Crear `deploy/certstream/docker-compose.yml`: servicio `certstream-server-go` (fijar imagen/tag), red interna, `restart: unless-stopped`, puerto ws local. — Build desde el repo git oficial (sin tag de registry publicado y verificado, ver Open Question de design.md); `docker compose config` valida sintaxis.
- [x] 4.2 Crear `deploy/certstream/README.md`: levantar + apuntar `VIGIA_CERTSTREAM_URL` al ws local.
- [x] 4.3 Documentar `VIGIA_CERTSTREAM_URL` self-hosted en `.env.example` (si existe).

## Phase 5: Testing

- [x] 5.1 `tests/`: CRUD `/watched-brands` — aislamiento por tenant (spec: listado aislado), 422 marca vacía, 404 baja ajena. (`tests/test_watched_brands_crud.py`, 6 tests)
- [x] 5.2 `tests/`: `_build_variant_map()` incluye variantes de marca activa; marca que hace fallar `generate_domain_variants` (mock) no rompe el mapa. (`tests/test_certstream_variant_map.py`, 5 tests)
- [x] 5.3 Manual: panel muestra solo findings CertStream y estado vacío correcto; alta/baja de marca refresca; cambio de estado persiste (sin infra de test frontend). — Verificado por lectura de código (mismo patrón que `RemediationBoard.tsx`/`fase2b`, sin infra de test frontend en el repo); no ejecutado en navegador real en esta sesión.
- [x] 5.4 Infra (solo con Docker): levantar el compose y confirmar que el listener se conecta al ws local; feed caído no tumba la API. — Docker CLI presente en esta máquina pero el daemon (Docker Desktop) no está corriendo (`open //./pipe/dockerDesktopLinuxEngine: no encontrado`); `docker compose config` valida la sintaxis del compose pero el levantamiento real queda PENDIENTE de validación en un host con el daemon activo. No bloquea el resto del change (ver design.md, degradación ya existente si el feed es inalcanzable).

## Phase 6: Cleanup / Coordinación

- [x] 6.1 `ruff` (backend) y `tsc -b`/build (frontend) limpios.
- [x] 6.2 Confirmar que `sdd-apply` no tocó `vigia_core_private/`.
- [x] 6.3 Re-verificar merges sobre `api/main.py`, `api.ts`, `Dashboard.tsx` tras cualquier change hermano aplicado antes (ver design.md "Coordinación"). — No aplica todavía: este `sdd-apply` corrió aislado en su propio worktree; el orquestador debe re-verificar tras mergear los changes hermanos (fase3a/3e/4/5/6/7).
