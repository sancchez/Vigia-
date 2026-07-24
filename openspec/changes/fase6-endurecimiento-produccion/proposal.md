# Proposal: Endurecimiento para producción (Fase 6)

## Intent

Fase 6 del plan entregable: cerrar la brecha entre "demo que corre en una
laptop" y "SaaS multi-tenant que un cliente paga mensual". No agrega
capacidades de detección nuevas — endurece las que ya existen para que
operen sin la CLI de Claude, con costo medible, escaneo activo continuo
seguro, cobro real, onboarding pulido y un entregable ejecutivo
consolidado.

## Recomendación de split (para el orquestador — NO ejecutar aún)

Este es el change más heterogéneo del plan (6 items, 3 áreas técnicas
distintas, uno de ellos bloqueado por una decisión de producto). **Se
recomienda partirlo en 3 sub-changes** con este orden, en vez de un solo
`sdd-apply` monolítico. El spec/design/tasks de este documento están
organizados por estos tres bloques para que el corte sea limpio:

| Sub-change sugerido | Contenido | Justificación |
|---|---|---|
| `fase6a-operacion-produccion` | LLM de producción + monitoreo de costo por reporte; escaneo activo programado seguro; UI de programación por activo | Cohesión "operar el servicio en prod de forma segura y medible". Backend-pesado. Desbloqueado por Fase 0 (columnas `autorizacion_activa` ya existen). Sin bloqueadores de producto. |
| `fase6b-billing-wompi` | Facturación Wompi wireada a `subscriptions` | **BLOQUEADO POR DECISIÓN DE PRODUCTO.** `HANDOFF.md` (línea 165) dice explícito: "No retomar la integración de pagos (Wompi) sin que el usuario lo pida — está en pausa a propósito". Aislarlo permite que el usuario dé luz verde a esto sin frenar 6a/6c. **No debe aplicarse hasta consentimiento explícito del usuario en chat.** |
| `fase6c-onboarding-y-export` | Onboarding guiado de verificación de dominio (UI; lógica backend ya existe); export ejecutivo consolidado multi-activo | Cohesión "pulido de experiencia de cliente". Frontend-forward, reusa backend existente (`tools/asset_verification.py`, `tools/report_export.py`, `GET /kpis` de fase2c). Bajo riesgo. |

Razón para 3 y no 1: aislar el bloqueo de Wompi (no arrastra a los otros
dos), separar backend de infra (6a) del pulido de UI/reporte (6c), y
reducir la superficie de conflicto con los changes hermanos en paralelo
(`fase3a/3b/3e/4/5/7`) que también tocan `api/main.py` y el frontend.

## Scope

### In Scope
- **LLM producción + costo**: registro de uso/costo por llamada y por
  reporte sobre `agents/_llm.py::call_claude` (hoy no mide nada); tabla
  `llm_usage` + endpoint de lectura + widget. Documentar el camino
  `ANTHROPIC_API_KEY`/OpenAI ya implementado como el de producción y
  desaconsejar el fallback CLI en despliegue.
- **Escaneo activo programado seguro**: el scheduler (`api/scheduler.py`,
  hoy SOLO recon pasivo) dispara además escaneo activo **solo** sobre
  activos con `assets.autorizacion_activa = 1`, reusando
  `tools/zap_api.py::run_checkpointed_active_scan`.
- **UI + config de programación** por activo (frecuencia; activo/pausado).
- **Wompi** (sub-change aislado, gated): checkout + webhook wireados a
  `subscriptions` (tabla y planes `trial/costero/flota/armada` ya existen).
- **Onboarding de verificación de dominio**: flujo UI pulido sobre la
  lógica DNS-TXT/HTTP ya implementada (`POST /assets/{id}/verify`).
- **Export ejecutivo consolidado** multi-activo (PDF/DOCX) reusando
  `tools/report_export.py` y los KPIs de fase2c.

### Out of Scope
- Nuevas capacidades de detección o de anti-suplantación (Fases 3/4).
- Envío de notificaciones (Fase 5) — el monitoreo de costo puede exponer
  un umbral, pero alertar sobre él es de Fase 5.
- Feed CertStream self-hosted (Fase 3c).
- Cambiar el vocabulario de `estado`/SLA de findings (fase2a/2b/2c).
- Tuning de prompts o mapeo ISO/Ley (vive en `vigia_core_private/`).

## Approach

Reusar todo lo ya construido y solo cerrar los cables sueltos: envolver
`call_claude` con un registrador de uso (patrón aditivo, no rompe los 7
call sites), extender el scheduler para ramificar activo/pasivo según una
columna que ya existe, y montar UI sobre endpoints que ya responden. Los
cambios de DB siguen el patrón portable SQLite→Postgres con reconciliación
en `db/connection.py` (igual que las columnas de Fase 0). Ningún prompt de
razonamiento se toca — no hay cruce con `vigia_core_private/`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `agents/_llm.py` | Modified | Hook de registro de tokens/costo alrededor de `call_claude` (6a) |
| `db/schema.sql` + `db/connection.py` | Modified | Tabla `llm_usage`; columnas de programación en `assets` (o tabla `scan_schedules`); reconciliación idempotente (6a) |
| `api/scheduler.py` | Modified | Rama de escaneo activo gated por `autorizacion_activa`; frecuencia por activo (6a) |
| `api/main.py` | Modified | Endpoints: uso/costo (6a), CRUD de programación (6a), checkout+webhook Wompi (6b), export ejecutivo (6c) |
| `frontend/src/` | New/Modified | Widget de costo + UI de programación (6a); página billing (6b); wizard de onboarding + botón export ejecutivo (6c) |
| `tools/report_export.py` | Read-only/Modified | Reuso para el export consolidado; posible helper de agregación multi-activo (6c) |
| `tools/asset_verification.py` | Read-only | El onboarding solo consume la lógica ya existente (6c) |
| `vigia_core_private/` | **No tocado** | Fase 6 es 100% infraestructura/UI; sin prompts ni mapeo ISO |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Wompi se aplica sin luz verde del usuario (viola HANDOFF.md) | Med | Aislado en `fase6b`; marcado como gated en proposal/design/tasks; `sdd-apply` de 6b prohibido sin consentimiento explícito en chat |
| El fallback CLI no reporta tokens (subprocess de `claude -p`) → costo no medible en modo demo | High | Documentado: el monitoreo de costo aplica a los backends de API (Anthropic/OpenAI); en CLI se registra "sin costo/no medible", no se finge un número |
| Escaneo activo programado corre sin autorización real | Low | Gate duro por `autorizacion_activa=1` antes de invocar `run_checkpointed_active_scan`; mismo espíritu que el gate pasivo ya existente |
| Conflicto en `api/main.py`/frontend con changes hermanos en paralelo | High | Split en 3; endpoints/archivos nuevos aditivos; el orquestador secuencia `sdd-apply` para evitar edición concurrente del mismo archivo |
| Costo real de ZAP activo recurrente (CPU/tiempo) sobre flota grande | Med | Frecuencia configurable por activo con default conservador; el gate de autorización limita el universo elegible |

## Rollback Plan

Todo es aditivo. Revertir por bloque: quitar el hook de uso de
`agents/_llm.py` y dropear `llm_usage` (6a); revertir la rama activa del
scheduler dejándolo en solo-pasivo como hoy (6a); eliminar rutas Wompi y
dejar los env `WOMPI_*` inertes como están (6b); quitar el wizard de
onboarding y el endpoint de export ejecutivo (6c). Las columnas de DB
nuevas son nullable/con default → una DB existente no se corrompe al
revertir el código. Sin migración de datos destructiva que deshacer.

## Dependencies

- **Fase 0** (aplicada): `assets.autorizacion_activa/_doc_url/_por/_en` —
  desbloquea el escaneo activo programado. `subscriptions` +
  `watched_brands` + `notifications` ya en `schema.sql`.
- **fase2c** (aplicada): `GET /kpis` y `tools/report_export.py` — el
  export ejecutivo consolidado agrega sobre esto.
- **fase2a/2b** (aplicadas): vocabulario de `estado`/SLA de findings que
  el export ejecutivo resume; este change los consume, no los modifica.
- Changes hermanos `fase3a/3b/3e/4/5/7` planificándose en paralelo: no
  visibles; el orquestador secuencia el `sdd-apply` para evitar conflicto
  en `api/main.py` y el frontend.
- **Producto**: `fase6b` (Wompi) depende de un "sí" explícito del usuario.

## Success Criteria

- [ ] `call_claude` registra tokens/costo por llamada en backends de API;
      un endpoint devuelve costo agregado por reporte y por tenant.
- [ ] El scheduler dispara escaneo activo SOLO sobre activos con
      `autorizacion_activa=1`; el resto sigue solo-pasivo; probado.
- [ ] La UI permite fijar frecuencia de escaneo por activo.
- [ ] (Gated) Wompi: checkout y webhook actualizan `subscriptions.estado`
      — NO aplicado sin consentimiento explícito del usuario.
- [ ] Onboarding guía la verificación DNS-TXT/HTTP hasta `verificado`,
      reusando la lógica backend existente.
- [ ] Export ejecutivo consolidado multi-activo descargable (PDF/DOCX).
- [ ] Migración de DB idempotente en SQLite y Postgres; suite pytest verde.
- [ ] `vigia_core_private/` no fue tocado.
