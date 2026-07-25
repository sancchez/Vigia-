# Design: Notificaciones y alertas

## Technical Approach

Módulo nuevo `api/notificaciones.py` con un despachador central
`enviar_notificacion(conn, tenant_id, evento, recurso_id, contexto)` que: (1)
consulta `notification_rules` activas del tenant para ese `evento`, (2) chequea
idempotencia contra `notifications` por `tenant_id+evento+recurso_id`, (3) arma
el cuerpo de texto plano, (4) envía por `smtplib`, (5) inserta fila en
`notifications` (`enviado_en` = ahora si OK, NULL si falló/omitido). Todo el
envío va envuelto en try/except y NUNCA propaga excepción al llamante.

Tres call-sites lo invocan, siguiendo patrones ya existentes:
- **Findings críticos y dominios clon**: en el path de persistencia de findings.
  Fase 2a centralizó la inserción en `_registrar_findings_con_ciclo_de_vida`
  (`api/main.py`); ese es el hook natural para el finding crítico. El dominio
  clon se dispara desde `api/certstream_listener.py` tras su `INSERT`.
- **Escalamiento de SLA**: job periódico nuevo en `api/scheduler.py`
  (`run_sla_escalation_once`), registrado en `start_scheduler()` con el mismo
  patrón `add_job("interval", ...)` que `run_scan_cycle_once`.

## Architecture Decisions

| Decisión | Opciones | Elegido | Rationale |
|---|---|---|---|
| Mecanismo de email | `smtplib` (stdlib) vs `aiosmtplib`/SendGrid/SES SDK | **`smtplib`** | No agrega dependencias a `pyproject.toml` (regla de la tarea). El envío es best-effort y de bajo volumen; no necesita async. SMTP saliente cubre Gmail/SES/Mailgun vía credenciales. |
| Config SMTP | Variables de entorno vs tabla DB | **env vars** (`VIGIA_SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS`) | Mismo patrón que `VIGIA_SCAN_INTERVAL_HOURS`/`CORS_ORIGINS`. Sin secreto en DB. Si falta `HOST`, el envío se omite limpio (loguea, no falla). |
| Idempotencia | Tabla nueva vs reusar `notifications` | **Reusar `notifications`** | La tabla ya existe (Fase 0) y su fila POR evento enviado ES el registro natural. Clave lógica `tenant_id+evento+destino+recurso`; `recurso` se codifica en `destino`-adyacente o en un `evento` compuesto (ver Interfaces). Sin columna nueva = aditivo puro. |
| Umbral SLA doble (75%/vencido) | Una fila vs dos | **`evento` compuesto** `sla_escalamiento:75pct` y `sla_escalamiento:vencido` | Cada umbral se notifica una sola vez; codificar el umbral en el string del evento persiste ambos hitos sin columna nueva y mantiene idempotencia por la misma consulta. |
| Trigger de finding crítico | Hook en `_registrar_findings_con_ciclo_de_vida` vs job de barrido | **Hook inline** | El finding crítico se conoce en el momento del INSERT; barrer sería redundante. Solo el SLA (temporal) necesita job periódico. |
| Slack/webhook en v1 | Incluir vs diferir | **Diferir** (follow-up) | El plan lo marca "opcional". `canal` se valida a `email`; el despachador queda con un dispatch por `canal` fácil de extender. Reduce superficie y riesgo de conflicto en esta ronda. |

## Data Flow

```
Inserción finding (api/main.py::_registrar_findings_con_ciclo_de_vida)
   └─ si severidad='critical' y es nuevo ─→ enviar_notificacion(evento='hallazgo_critico_nuevo', recurso=finding_id)

CertStream (api/certstream_listener.py, tras INSERT)
   └─ enviar_notificacion(evento='dominio_clon_detectado', recurso=finding_id)

Scheduler (api/scheduler.py::run_sla_escalation_once, cada N horas)
   └─ SELECT findings abiertos con sla_due_at ─→ por umbral cruzado ─→ enviar_notificacion(evento='sla_escalamiento:{75pct|vencido}', recurso=finding_id)

enviar_notificacion:
   reglas activas? ──no──> return (silencio)
        │sí
   ya en notifications (tenant+evento+recurso)? ──sí──> return (idempotente)
        │no
   smtplib.send ──ok──> INSERT notifications(enviado_en=now)
        └──falla/no-config──> log + INSERT notifications(enviado_en=NULL)
```

## File Changes (contrato para `sdd-apply`)

| File | Action | Description |
|------|--------|-------------|
| `api/notificaciones.py` | **Create** | `enviar_notificacion(...)`, `_enviar_email_smtp(...)`, `_reglas_activas(...)`, `_ya_enviado(...)`, config SMTP desde env |
| `api/scheduler.py` | Modify | `run_sla_escalation_once()` + `add_job` en `start_scheduler()` (intervalo `VIGIA_SLA_CHECK_INTERVAL_HOURS`, default 6) |
| `api/main.py` | Modify | Hook a `enviar_notificacion` en `_registrar_findings_con_ciclo_de_vida` (finding crítico); CRUD `GET/POST/PATCH/DELETE /notification-rules`; modelos `NotificationRuleIn/Out` |
| `api/certstream_listener.py` | Modify | Llamada a `enviar_notificacion(evento='dominio_clon_detectado')` tras el INSERT existente |
| `frontend/src/api.ts` | Modify | Tipos `NotificationRule` + métodos CRUD |
| `frontend/src/components/NotificationRules.tsx` | **Create** | UI de reglas (lista + form crear/activar/desactivar), montada en `Dashboard.tsx` |
| `frontend/src/pages/Dashboard.tsx` | Modify | Montar `NotificationRules` |
| `tests/test_notificaciones.py` | **Create** | Reglas, idempotencia, best-effort ante fallo SMTP (mock), canal no soportado |
| `tests/test_sla_escalamiento.py` | **Create** | Umbral 75% y vencido, una vez por umbral, finding cerrado no dispara |

**Sin cambios en `db/schema.sql`/`db/connection.py`**: `notifications`/`notification_rules` ya existen (Fase 0). No requiere columnas nuevas.

## Interfaces / Contracts

```python
def enviar_notificacion(
    conn, tenant_id: str, evento: str, recurso_id: str,
    asunto: str, cuerpo: str,
) -> bool: ...  # True si se envió; best-effort, nunca lanza

# Idempotencia: existe fila en notifications con (tenant_id, evento, destino, evento LIKE recurso)?
#   -> recurso_id se incluye en `evento` compuesto o se filtra por destino+evento+created_at ventana.
#   Decisión concreta: columna `evento` guarda "hallazgo_critico_nuevo", y el
#   recurso se distingue guardando `destino` + un sufijo en `evento` NO -- en su
#   lugar la query de idempotencia filtra por evento + destino + un match del
#   recurso codificado en el cuerpo/una convención. Ver Open Question #1.

class NotificationRuleIn(BaseModel):
    evento: str        # hallazgo_critico_nuevo | dominio_clon_detectado | sla_escalamiento
    canal: str         # 'email' (v1); otros -> 422
    destino: str       # email destino
    is_active: bool = True
```

Eventos canónicos (v1): `hallazgo_critico_nuevo`, `dominio_clon_detectado`,
`sla_escalamiento`. El scheduler internamente distingue umbral `75pct`/`vencido`
para idempotencia, pero la REGLA del tenant se configura al nivel
`sla_escalamiento` (ambos umbrales comparten regla/destino).

## Testing Strategy

| Layer | Qué | Cómo |
|---|---|---|
| Unit | Evaluación de reglas + idempotencia | Insertar reglas/notifications, verificar decisión de envío sin SMTP real |
| Unit | Umbrales SLA (75%/vencido/no-disparo) | Findings con `created_at`/`sla_due_at` controlados, correr `run_sla_escalation_once` |
| Integration | Best-effort ante fallo SMTP | Mockear `smtplib.SMTP` para lanzar; verificar que el llamante no rompe y `enviado_en` queda NULL |
| Integration | CRUD `/notification-rules` | 200/404 cross-tenant/422 canal no soportado |
| Regresión | Suite completa | `pytest tests/` verde; frontend `tsc -b && vite build` |

## Migration / Rollout

No requiere migración: las tablas existen vacías desde Fase 0. Aditivo puro.
`git revert` sin downtime. Sin SMTP configurado, el sistema funciona igual que
hoy (envío se omite silenciosamente) — no bloquea despliegues sin correo.

## Open Questions (resueltas en `sdd-apply`)

- [x] **Clave exacta de idempotencia** en `notifications` sin columna nueva:
  **Resuelto -- Opción A**, implementada tal cual (`api/notificaciones.py`).
  La columna `evento` guarda `"{evento}:{recurso_id}"` (ej.
  `"hallazgo_critico_nuevo:f123"`, `"sla_escalamiento:75pct:f123"`); el match
  de `notification_rules` usa solo la porción antes del primer `:`
  (`_evento_base`). La clave de idempotencia real terminó siendo
  `(tenant_id, evento_compuesto, destino)` -- no solo `(tenant_id, evento,
  recurso)` -- para que dos reglas del mismo evento con destinos distintos se
  traten como notificaciones independientes (cada destino se envía y registra
  por separado), decisión no explícita en el design original pero consistente
  con la fila-por-envío de `notifications`.
- [x] **[RIESGO ABIERTO — dependencia `fase3b`]** Confirmado al releer
  `api/certstream_listener.py` en esta sesión de `sdd-apply` (Task 0.2): el
  `tipo='dominio_variante_certstream'` sigue siendo exactamente el que el
  listener escribe hoy, sin cambios de `fase3b` visibles en este worktree
  aislado. El hook quedó anclado a ese `tipo` tal como estaba planeado. Si al
  mergear se descubre que `fase3b` renombró el tipo/severidad, el orquestador
  solo necesita ajustar el predicado en
  `api/certstream_listener.py::registrar_finding_certstream` (la llamada a
  `enviar_notificacion` ya está ahí, justo después del `INSERT`) -- no hace
  falta tocar `api/notificaciones.py`.
- [x] ¿El aviso `sla_escalamiento:vencido` debe además transicionar el `estado`
  del finding? **Diferido, como estaba previsto** -- v1 solo notifica, no muta
  `findings` (evita colisión con la máquina de estados de `fase2a`).
