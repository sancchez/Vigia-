# Proposal: Notificaciones y alertas (Fase 5)

## Intent

Hoy Vigia detecta, pero el cliente no se entera hasta que abre el panel — sin
alertas activas no es "vigilancia". Falta cerrar el lazo de aviso ante los
eventos que importan: hallazgo crítico nuevo, dominio clon detectado, y
escalamiento de SLA (aviso al 75% del plazo, escalar al vencer). Las tablas
`notifications` y `notification_rules` ya existen desde Fase 0 pero nada las
lee ni escribe.

## Scope

### In Scope
- Módulo `api/notificaciones.py`: evaluación de `notification_rules` por tenant + despacho.
- Envío por **email vía `smtplib`** (stdlib, sin dependencias nuevas) con config SMTP por variables de entorno.
- 3 eventos disparadores: `hallazgo_critico_nuevo`, `dominio_clon_detectado`, `sla_escalamiento` (75% y vencido).
- Job APScheduler nuevo en `api/scheduler.py` que barre `findings.sla_due_at` periódicamente.
- Registro idempotente de lo enviado en `notifications` (evitar doble-envío).
- CRUD de `notification_rules` en `api/main.py` + UI de configuración en frontend.

### Out of Scope
- Canal Slack/webhook: se deja como follow-up explícito (v1 = solo email). El diseño reserva `canal` para no cerrarle la puerta.
- Digest/agrupación de alertas (una por evento en v1).
- Plantillas HTML ricas / branding (email de texto plano en v1).
- Verificación de entregabilidad (bounces, SPF del propio SMTP).

## Approach

Un despachador central `enviar_notificacion(tenant_id, evento, contexto)` que
consulta `notification_rules` activas del tenant para ese `evento`, arma el
cuerpo, envía por SMTP y registra en `notifications`. Tres call-sites lo
invocan: inserción de finding crítico y de dominio clon (en el path de
persistencia de findings), y un job periódico de SLA en el scheduler (mismo
patrón que `run_scan_cycle_once`). Idempotencia por consulta a `notifications`
antes de enviar (una fila por tenant+evento+clave-de-recurso).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `api/notificaciones.py` | New | Despachador, envío SMTP, evaluación de reglas, idempotencia |
| `api/scheduler.py` | Modified | Job nuevo `run_sla_escalation_once` (barrido de `sla_due_at`) |
| `api/main.py` | Modified | CRUD `notification_rules`; hook de envío en persistencia de findings |
| `frontend/src/` | New/Modified | UI de reglas de notificación + tipos/métodos en `api.ts` |
| `db/schema.sql` | Read-only | Tablas ya existen (Fase 0); no se modifican |
| `tests/` | New | Envío mockeado, evaluación de reglas, idempotencia, escalamiento SLA |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Contrato del finding de dominio clon lo fija `fase3b` (en paralelo, no visible) | Med | Depender del `tipo='dominio_variante_certstream'` que el listener YA escribe; marcar como riesgo abierto no bloqueante |
| Doble-envío ante reinicios/escaneos repetidos | Med | Idempotencia por `notifications` (tenant+evento+recurso) antes de enviar |
| SMTP mal configurado tumba el flujo de escaneo | Med | Envío best-effort, aislado en try/except, nunca propaga al path de persistencia |
| Conflicto en `api/main.py`/`api/scheduler.py` con changes hermanos en paralelo | High | Módulo nuevo separado; hooks mínimos y localizados; aplicar tras merge de hermanos |

## Rollback Plan

Aditivo: borrar `api/notificaciones.py`, el job nuevo del scheduler, el CRUD y
el hook de envío en `main.py`, y la UI. Sin migración de datos (las tablas ya
existían vacías). `git revert` sin downtime.

## Dependencies

- `fase3b` (paralelo): fija el contrato real del finding de dominio clon. **Riesgo abierto no bloqueante** — v1 se ancla al `tipo` que el listener ya emite.
- Fase 0 (mergeada): tablas `notifications`/`notification_rules`. Fase 2a (mergeada): `sla_due_at` en `findings`.

## Success Criteria

- [ ] Email real enviado (SMTP) ante finding crítico nuevo, dominio clon, y SLA al 75%/vencido, según `notification_rules` del tenant.
- [ ] Sin dependencias nuevas en `pyproject.toml` (solo `smtplib` stdlib).
- [ ] Idempotencia probada: mismo evento no re-envía.
- [ ] Fallo de SMTP no rompe escaneo ni scheduler (best-effort, aislado).
- [ ] UI permite crear/activar/desactivar reglas por evento y destino.
