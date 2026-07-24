# Tasks: Notificaciones y alertas

> Gate previo (condición de arranque, no una tarea): antes de `sdd-apply`,
> confirmar que los changes hermanos que comparten `api/main.py`/`api/scheduler.py`
> (`fase3a`, `fase3b`, `fase4`, `fase6`) que se estén aplicando en paralelo ya
> mergearon, y releer el código real de `_registrar_findings_con_ciclo_de_vida`
> (fase2a) y del `INSERT` de `api/certstream_listener.py` — son los hooks
> compartidos. Confirmar el `tipo` real del finding de dominio clon (dependencia
> `fase3b`, ver design.md Open Question #2).

## Phase 0: Pre-flight (bloqueante)

- [ ] 0.1 Confirmar tablas `notifications`/`notification_rules` presentes en la DB real (Fase 0 mergeada); no requieren cambios de schema.
- [ ] 0.2 Releer firma real de `_registrar_findings_con_ciclo_de_vida` y el INSERT de `certstream_listener.py`; ajustar puntos de hook en design.md si difieren.
- [ ] 0.3 Resolver Open Question #1 (clave de idempotencia): decidir Opción A (recurso en `evento` compuesto, sin columna nueva) salvo bloqueo real.

## Phase 1: Módulo de despacho (`api/notificaciones.py`)

- [ ] 1.1 Crear `api/notificaciones.py`; leer config SMTP de env (`VIGIA_SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS`).
- [ ] 1.2 `_reglas_activas(conn, tenant_id, evento)`: SELECT de `notification_rules` activas (match de regla por evento base, ignorando sufijo `:umbral`).
- [ ] 1.3 `_ya_enviado(conn, tenant_id, evento, recurso_id)`: consulta idempotencia sobre `notifications`.
- [ ] 1.4 `_enviar_email_smtp(destino, asunto, cuerpo)`: `smtplib` con TLS opcional; retorna bool, no lanza.
- [ ] 1.5 `enviar_notificacion(conn, tenant_id, evento, recurso_id, asunto, cuerpo)`: orquesta reglas→idempotencia→envío→INSERT `notifications` (`enviado_en` now/NULL); todo en try/except, best-effort.

## Phase 2: Disparadores

- [ ] 2.1 Hook en `_registrar_findings_con_ciclo_de_vida` (`api/main.py`): si finding nuevo y `severidad='critical'`, llamar `enviar_notificacion(evento='hallazgo_critico_nuevo')`.
- [ ] 2.2 Hook en `api/certstream_listener.py` tras el INSERT: `enviar_notificacion(evento='dominio_clon_detectado')`.
- [ ] 2.3 `run_sla_escalation_once()` en `api/scheduler.py`: SELECT findings abiertos con `sla_due_at`; por cada umbral cruzado (75%/vencido) llamar `enviar_notificacion(evento='sla_escalamiento:{umbral}')`.
- [ ] 2.4 Registrar el job en `start_scheduler()` (`add_job("interval", hours=VIGIA_SLA_CHECK_INTERVAL_HOURS, default 6, id="vigia_sla_escalamiento", replace_existing=True)`).

## Phase 3: CRUD + Frontend

- [ ] 3.1 Modelos `NotificationRuleIn/Out` en `api/main.py`; validar `canal` (solo `email`, else 422).
- [ ] 3.2 Endpoints `GET/POST/PATCH/DELETE /notification-rules` scoped por tenant (404 cross-tenant).
- [ ] 3.3 `frontend/src/api.ts`: tipo `NotificationRule` + métodos CRUD.
- [ ] 3.4 Crear `frontend/src/components/NotificationRules.tsx` (lista + form crear/activar/desactivar/eliminar).
- [ ] 3.5 Montar `NotificationRules` en `frontend/src/pages/Dashboard.tsx`.

## Phase 4: Testing

- [ ] 4.1 `tests/test_notificaciones.py`: envío con regla activa (mock SMTP) → fila `notifications` con `enviado_en`; sin regla → nada.
- [ ] 4.2 `tests/test_notificaciones.py`: idempotencia (segundo disparo del mismo recurso no re-envía).
- [ ] 4.3 `tests/test_notificaciones.py`: fallo SMTP (mock lanza) → llamante no rompe, `enviado_en` NULL; SMTP no configurado → omisión limpia.
- [ ] 4.4 `tests/test_notificaciones.py`: CRUD `/notification-rules` (200/404 cross-tenant/422 canal no soportado).
- [ ] 4.5 `tests/test_sla_escalamiento.py`: umbral 75% dispara una vez; vencido dispara una vez; cerrado antes de umbral no dispara.
- [ ] 4.6 Suite completa `pytest tests/` verde (sin romper fase2a/2b/2c); frontend `tsc -b && vite build` compila.

## Phase 5: Cleanup

- [ ] 5.1 Documentar variables SMTP en `.env.example`/README de `api/` y el follow-up de Slack/webhook.
- [ ] 5.2 Marcar en design.md la resolución final de las Open Questions.
