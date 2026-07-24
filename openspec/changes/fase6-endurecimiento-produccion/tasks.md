# Tasks: Endurecimiento para producción (Fase 6)

> Agrupadas por los tres sub-changes recomendados en `proposal.md`. Si el
> orquestador aprueba el split, cada grupo (6a/6b/6c) se convierte en su
> propio change y su propio `sdd-apply`. **El grupo 6b (Wompi) NO se ejecuta
> sin consentimiento explícito del usuario** (`HANDOFF.md` línea 165).
> Ningún grupo toca `vigia_core_private/`.

---

## Grupo 6a — `fase6a-operacion-produccion`

### 6a.1 Infraestructura — DB
- [ ] 6a.1.1 Agregar tabla `llm_usage` a `db/schema.sql` (`CREATE TABLE IF
      NOT EXISTS`, índices por `tenant_id` y `scan_id`) — ver design.md 6a.2.
- [ ] 6a.1.2 Agregar columnas `scan_frequency_hours` (INTEGER null) y
      `scan_paused` (INTEGER default 0) al `CREATE TABLE assets` de
      `db/schema.sql`.
- [ ] 6a.1.3 En `db/connection.py`: añadir
      `_ensure_sqlite_assets_scheduling_columns(conn)` (gemelo de
      `_ensure_sqlite_assets_autorizacion_columns`) y la rama Postgres `ADD
      COLUMN IF NOT EXISTS`; registrar en la función de reconciliación que
      corre al conectar (junto a las tres `_ensure_*` ya existentes).
- [ ] 6a.1.4 Test de migración idempotente en SQLite y (si hay fixture)
      Postgres: correr dos veces, esquema estable, suite existente verde.

### 6a.2 Implementación — Monitoreo de costo LLM
- [ ] 6a.2.1 En `agents/_llm.py`: `ContextVar` de contexto
      (`{tenant_id, scan_id}`) + helper `set_llm_context(...)`.
- [ ] 6a.2.2 Tabla de tarifas por model id (Sonnet, `gpt-5.4-mini`, etc.,
      valores de `docs/despliegue.md`) y cálculo de `costo_estimado`.
- [ ] 6a.2.3 Registrar en `llm_usage` tras cada `_call_via_api` /
      `_call_via_openai_api` (tokens de `usage_metadata`); registrar CLI con
      `costo_estimado=null`, `provider="cli"`. Sin cambiar el retorno de
      `call_claude` ni los 7 call sites.
- [ ] 6a.2.4 Poblar el contexto donde arranca un scan/reporte (`POST /scan`,
      `POST /scan/activo`, `api/scheduler.py`) vía `set_llm_context`.
- [ ] 6a.2.5 `GET /costos` (o `/usage`): agregado por `scan_id` y total del
      tenant, scoped por `tenant_id`.
- [ ] 6a.2.6 Documentar en `docs/despliegue.md`: el fallback CLI no mide
      costo; producción usa `ANTHROPIC_API_KEY`/OpenAI.

### 6a.3 Implementación — Escaneo activo programado seguro
- [ ] 6a.3.1 En `api/scheduler.py::run_scan_cycle_once`: ampliar el `SELECT`
      a `autorizacion_activa`, `scan_frequency_hours`, `scan_paused`.
- [ ] 6a.3.2 Saltar activos con `scan_paused=1` y los que aún no cumplen su
      `scan_frequency_hours` desde su último scan.
- [ ] 6a.3.3 Ramificar: `autorizacion_activa==1` (y verificado/exento) →
      `run_checkpointed_active_scan`, persistir scan con
      `autorizacion_firmada=1`; si no → recon pasivo como hoy. Mantener el
      gate `asset_autorizado_para_escanear` para ambos.
- [ ] 6a.3.4 Manejo de error del escaneo activo (ZAP no disponible): loguear
      y continuar el ciclo, nunca marcar autorización que no hubo.

### 6a.4 Implementación — API/UI de programación
- [ ] 6a.4.1 Endpoint de programación por activo (`PUT /assets/{id}/schedule`
      o extender `PATCH /assets/{id}`): setear frecuencia/pausa, scoped por
      tenant (404 para asset de otro tenant).
- [ ] 6a.4.2 `frontend/src/api.ts`: tipos + métodos para programación y
      `GET /costos`.
- [ ] 6a.4.3 UI: control de frecuencia/pausa por activo (en Dashboard o
      componente nuevo) + widget de costo por reporte/tenant.

### 6a.5 Testing 6a
- [ ] 6a.5.1 Registro de `llm_usage` con LLM mockeado (API y CLI).
- [ ] 6a.5.2 Agregación de `GET /costos` + aislamiento por tenant.
- [ ] 6a.5.3 Ramificación activo/pasivo del scheduler (ZAP mockeado):
      activo con `autorizacion_activa=1` → activo; =0 → pasivo; no
      verificado → omitido; pausado → omitido.
- [ ] 6a.5.4 `PUT schedule` scoped por tenant (404 cross-tenant).

---

## Grupo 6b — `fase6b-billing-wompi` (GATED — requiere luz verde del usuario)

### 6b.1 Implementación — Checkout
- [ ] 6b.1.1 `POST /billing/checkout` (auth): validar plan
      (`costero|flota|armada`) → 422 si inválido; crear referencia con
      `WOMPI_PRIVATE_KEY` server-side; `subscriptions.estado=pendiente`;
      devolver solo `WOMPI_PUBLIC_KEY` + referencia.

### 6b.2 Implementación — Webhook
- [ ] 6b.2.1 `POST /billing/webhook` (sin JWT): verificar firma con
      `WOMPI_EVENTS_SECRET` antes de todo; 4xx sin efecto si inválida.
- [ ] 6b.2.2 Firma válida + aprobado → actualizar
      `subscriptions.estado/plan/renovacion_en/wompi_subscription_id`;
      idempotente por id de transacción.
- [ ] 6b.2.3 Reflejar `plan` activo también en `tenants.plan` si el resto
      del código lo lee de ahí (ver `api/main.py` login/register).

### 6b.3 UI + testing 6b
- [ ] 6b.3.1 Página de billing en frontend: elegir plan → checkout →
      estado de suscripción.
- [ ] 6b.3.2 Tests: plan inválido → 422; webhook firma válida transiciona
      estado; firma inválida → 4xx sin cambio; idempotencia.

---

## Grupo 6c — `fase6c-onboarding-y-export`

### 6c.1 Implementación — Onboarding de verificación de dominio (UI)
- [ ] 6c.1.1 Wizard frontend: paso a paso DNS-TXT/HTTP consumiendo
      `GET /assets` (`instrucciones_verificacion`) y `POST /assets/{id}/
      verify`; mostrar resultado negativo con reintento; encadenar a
      "autorizar escaneo activo" al verificarse.
- [ ] 6c.1.2 (Opcional) `GET /onboarding/estado`: resumen de qué falta por
      tenant (activos sin verificar / sin autorización). Decidir si aporta
      o si el frontend lo deriva de `GET /assets`.

### 6c.2 Implementación — Export ejecutivo consolidado
- [ ] 6c.2.1 Backend: componer markdown consolidado multi-activo (postura
      por activo desde `findings` + KPIs vía la capa de `GET /kpis`).
- [ ] 6c.2.2 `GET /reports/ejecutivo/download?formato=pdf|docx` reusando
      `markdown_to_pdf_bytes`/`markdown_to_docx_bytes`; validación de
      formato → 422; scoped por tenant.
- [ ] 6c.2.3 `frontend/src/api.ts` + botón "Export ejecutivo" en Dashboard.

### 6c.3 Testing 6c
- [ ] 6c.3.1 Export ejecutivo con varios activos → PDF/DOCX no vacío;
      formato inválido → 422; aislamiento por tenant.
- [ ] 6c.3.2 (Manual/UI) recorrido del wizard de onboarding contra un asset
      real de prueba.

---

## Cierre (todos los grupos aplicados)
- [ ] Correr `pytest tests/` completo — baseline verde + tests nuevos.
- [ ] `cd frontend && npx tsc -b && npm run build` compila sin errores.
- [ ] Confirmar que `vigia_core_private/` no fue tocado (git status).
- [ ] Actualizar `docs/despliegue.md`/`docs/produccion-readiness.md` con lo
      que quedó cerrado de Fase 6.
