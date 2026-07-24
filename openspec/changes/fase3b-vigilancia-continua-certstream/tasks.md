# Tasks: Fase 3b+3c — Vigilancia continua (panel CertStream + feed self-hosted)

## Phase 1: Backend — CRUD watched_brands (`api/main.py`)

- [ ] 1.1 Agregar modelos `WatchedBrandIn` (`marca: str`) y `WatchedBrandOut` (`id, marca, is_active, created_at`).
- [ ] 1.2 `GET /watched-brands`: `WHERE tenant_id = ? ORDER BY created_at DESC` (patrón `listar_findings`).
- [ ] 1.3 `POST /watched-brands`: validar `marca` no-vacía tras strip (422 si vacía); dedup marca activa del tenant; `uuid.uuid4()` en Python.
- [ ] 1.4 `DELETE /watched-brands/{id}`: soft-delete `is_active=0` del tenant; 404 si es de otro tenant.
- [ ] 1.5 Insertar el bloque antes del catch-all SPA (~línea 2432); no tocar rutas existentes.

## Phase 2: Listener — consumir watched_brands (`api/certstream_listener.py`)

- [ ] 2.1 En `_build_variant_map()`, tras el bucle de assets, leer `watched_brands` activas por tenant (`SELECT ... WHERE is_active = 1`).
- [ ] 2.2 Normalizar marca→dominio candidato: si contiene `.` usar tal cual, si no anexar `.com`; pasar a `generate_domain_variants`.
- [ ] 2.3 Envolver cada marca en `try/except` (log + continuar), igual que el bucle de assets; contar/loguear omitidas.
- [ ] 2.4 Confirmar que el `refresh` periódico (`_ensure_variant_map`) reconstruye con las marcas nuevas (ya lo hace por diseño).

## Phase 3: Frontend — API + componentes

- [ ] 3.1 `frontend/src/api.ts`: tipo `WatchedBrand`; funcs `listWatchedBrands`, `createWatchedBrand(marca)`, `deleteWatchedBrand(id)`.
- [ ] 3.2 Crear `VigilanciaPanel.tsx`: recibe `findings`+`members`+`onChanged`; filtra `tipo === "dominio_variante_certstream"`; tabla (base, observado, cuándo, severidad, estado) reusando `.chip`/badge SLA de Fase 2b; estado vacío sin ruido.
- [ ] 3.3 En `VigilanciaPanel.tsx`, reusar `api.patchFinding` para cambiar estado/owner (mismo patrón revert que `RemediationBoard`).
- [ ] 3.4 Crear `WatchedBrandsConfig.tsx`: input+submit (crear), lista con baja; error visible; refresca vía `onChanged`.
- [ ] 3.5 `Dashboard.tsx`: importar+montar ambos; agregar `listWatchedBrands()` a `cargarTodo()` y pasar props.
- [ ] 3.6 `index.css`: solo si hace falta clase nueva (preferir reuso).

## Phase 4: Infra — feed self-hosted (3c)

- [ ] 4.1 Crear `deploy/certstream/docker-compose.yml`: servicio `certstream-server-go` (fijar imagen/tag), red interna, `restart: unless-stopped`, puerto ws local.
- [ ] 4.2 Crear `deploy/certstream/README.md`: levantar + apuntar `VIGIA_CERTSTREAM_URL` al ws local.
- [ ] 4.3 Documentar `VIGIA_CERTSTREAM_URL` self-hosted en `.env.example` (si existe).

## Phase 5: Testing

- [ ] 5.1 `tests/`: CRUD `/watched-brands` — aislamiento por tenant (spec: listado aislado), 422 marca vacía, 404 baja ajena.
- [ ] 5.2 `tests/`: `_build_variant_map()` incluye variantes de marca activa; marca que hace fallar `generate_domain_variants` (mock) no rompe el mapa.
- [ ] 5.3 Manual: panel muestra solo findings CertStream y estado vacío correcto; alta/baja de marca refresca; cambio de estado persiste (sin infra de test frontend).
- [ ] 5.4 Infra (solo con Docker): levantar el compose y confirmar que el listener se conecta al ws local; feed caído no tumba la API.

## Phase 6: Cleanup / Coordinación

- [ ] 6.1 `ruff` (backend) y `tsc -b`/build (frontend) limpios.
- [ ] 6.2 Confirmar que `sdd-apply` no tocó `vigia_core_private/`.
- [ ] 6.3 Re-verificar merges sobre `api/main.py`, `api.ts`, `Dashboard.tsx` tras cualquier change hermano aplicado antes (ver design.md "Coordinación").
