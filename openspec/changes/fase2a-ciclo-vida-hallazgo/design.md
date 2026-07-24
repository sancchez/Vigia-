# Design: Fase 2a — Ciclo de vida del hallazgo

## Technical Approach

Todo en `api/main.py` (mismo archivo que Fase 0/1), más dos tablas nuevas en
`db/schema.sql`/`db/connection.py` con el mismo patrón dual SQLite+Postgres
ya probado (`CREATE TABLE IF NOT EXISTS` + reconciliación explícita). Máquina
de estados en Python plano (sin LLM), mismo espíritu que `gate_autorizacion`.
Los dos paths de inserción (`scan()`, `_correr_escaneo_activo_en_background`)
se refactorizan para llamar una función compartida nueva que calcula SLA y
aplica drift antes de tocar `findings`.

## Architecture Decisions

| Decisión | Elegido | Alternativas | Rationale |
|---|---|---|---|
| Nota de auditoría | Tabla nueva `finding_transiciones` (1 fila por cambio) | Campo `findings.ultima_nota` simple | Se necesita historial completo (quién/cuándo/qué cambió), no solo la última nota — y lo reusará `fase2c` para MTTR (tiempo entre transiciones). Un campo simple perdería eso. |
| Config de SLA | Tabla nueva `tenant_sla_config` (1:1 con `tenants`, mismo patrón que `subscriptions`), con fallback a constante Python si no hay fila | Columnas en `tenants`; constante global sin tabla | El plan pide "configurable por tenant" explícito — una tabla propia evita ensuciar `tenants` con columnas de un solo dominio y permite defaults distintos sin migrar `tenants`. Constante Python (`DEFAULT_SLA_DIAS`) sigue siendo el fallback real hasta que un tenant configure su fila. |
| Extender `FindingOut`/`FindingDetailOut` con campos de ciclo de vida | Sí — `estado`, `owner_user_id`, `owner_email`, `sla_due_at`, `primera_vez_visto`, `ultima_vez_visto` | Dejarlo fuera (como Fase 1 hizo con `FindingDetailOut` para no inflar `FindingOut`) | Fase 1 evitó inflar `FindingOut` con datos derivados de `raw_json` (costoso de parsear). Estos son columnas SQL escalares ya existentes (gratis de leer) y son exactamente lo que un tablero por estado necesita en la vista de lista — sin esto, `fase2b` no podría pintar columnas por estado ni badges de SLA. `owner_email` vía LEFT JOIN a `users` para no obligar al frontend a resolver el email con una segunda llamada. |
| Poblar `scans.asset_id` | Sí, en ambos INSERT de `scans` | Dejarlo `null` y hacer drift por `target` string | La columna ya existe y hoy se descarta (`_asset` con underscore, resultado de `_asset_verificado_para_target`, nunca usado). Es la llave correcta para "mismo asset" — un `target` string puede variar (`http://` vs `https://`, con/sin `www`) mientras el asset resuelto es estable. |
| Clave de match para drift | `tenant_id + asset_id + tipo + endpoint` | Solo `tipo`+`endpoint` global | Sin `tenant_id`/`asset_id` se compararía entre tenants o activos distintos — riesgo de seguridad y de falsos positivos señalado en el proposal. |

## Data Flow

```
INSERCIÓN (scan() y _correr_escaneo_activo_en_background):

  hallazgo normalizado {tipo, severidad, endpoint, confirmado, raw_json}
       │
       ▼
  _registrar_findings_con_ciclo_de_vida(conn, tenant_id, scan_id, asset_id, hallazgo, now)
       │
       ├─ SELECT id, estado FROM findings
       │    WHERE tenant_id=? AND tipo=? AND endpoint=?
       │      AND scan_id IN (SELECT id FROM scans WHERE asset_id=? AND asset_id IS NOT NULL)
       │
       ├─ match encontrado ─┬─ estado NOT IN (cerrado,verificado,aceptado_riesgo)
       │                    │     → UPDATE findings SET ultima_vez_visto=now
       │                    └─ estado IN (cerrado,verificado,aceptado_riesgo)
       │                          → UPDATE estado='descubierto', ultima_vez_visto=now
       │                          → INSERT finding_transiciones (nota="reincidencia detectada", changed_by=NULL)
       │
       └─ sin match ─→ sla_due_at = _calcular_sla(tenant_id, severidad)
                        → INSERT INTO findings (..., sla_due_at, primera_vez_visto=now, ultima_vez_visto=now)

PATCH /findings/{id}:

  FindingPatchIn{estado?, owner_user_id?, nota?}
       │
       ├─ SELECT * FROM findings WHERE id=? AND tenant_id=?  → 404 si no existe
       ├─ si estado: validar TRANSICIONES_VALIDAS[actual] → 409 si inválida; nota requerida → 422 si falta
       ├─ si owner_user_id: SELECT 1 FROM users WHERE id=? AND tenant_id=? → 422 si no
       ├─ UPDATE findings SET estado=?, owner_user_id=?
       ├─ INSERT INTO finding_transiciones (estado_anterior, estado_nuevo, owner_anterior, owner_nuevo, nota, changed_by=user.user_id)
       └─ 200 con FindingOut actualizado (incluye owner_email vía LEFT JOIN)
```

## File Changes

| File | Action | Description |
|------|--------|--------------|
| `db/schema.sql` | Modify | 2 `CREATE TABLE IF NOT EXISTS` nuevos: `finding_transiciones`, `tenant_sla_config` + índices |
| `db/connection.py` | Modify | Reconciliación SQLite (`_ensure_sqlite_*`, aunque tablas nuevas no requieren ALTER — solo falta en DBs viejas si `executescript` no corrió; se agrega por paridad con el patrón) + `_get_pg_conn` ya corre `schema.sql` completo, sin ALTER extra necesario (tablas nuevas, no columnas nuevas en tablas existentes) |
| `api/main.py` | Modify | `FindingOut`/`FindingDetailOut` extendidos; `FindingPatchIn`, `TRANSICIONES_VALIDAS`, `DEFAULT_SLA_DIAS`, `_calcular_sla_due_at`, `_registrar_findings_con_ciclo_de_vida`, `PATCH /findings/{id}`; `scan()` y `_correr_escaneo_activo_en_background` usan el helper nuevo en vez del `INSERT` directo; ambos pasan `asset["id"]` (ya resuelto, hoy descartado) al `INSERT INTO scans` |
| `tests/` | New | `test_finding_patch.py`, `test_sla_calculo.py`, `test_drift_reincidencia.py` |

## Interfaces / Contracts

**Contrato exacto para `fase2b-tablero-remediacion` — fijo, no cambia sin nueva ronda de diseño:**

```python
class FindingOut(BaseModel):
    id: str
    scan_id: str
    tipo: str
    severidad: str
    endpoint: str
    confirmado: bool
    created_at: str
    estado: str                              # NUEVO
    owner_user_id: Optional[str] = None       # NUEVO
    owner_email: Optional[str] = None         # NUEVO (derivado, LEFT JOIN users)
    sla_due_at: Optional[str] = None          # NUEVO
    primera_vez_visto: Optional[str] = None   # NUEVO
    ultima_vez_visto: Optional[str] = None    # NUEVO

# FindingDetailOut (GET /findings/{id}) gana los mismos 6 campos, mismos nombres/tipos.

class FindingPatchIn(BaseModel):
    estado: Optional[str] = None          # uno de los 8 valores del enum
    owner_user_id: Optional[str] = None   # debe pertenecer al mismo tenant
    nota: Optional[str] = None            # OBLIGATORIO (min_length=1) si se envía `estado`

# PATCH /findings/{finding_id}  (response_model=FindingOut, 200)
# Errores: 404 (no existe / otro tenant), 409 (transición inválida),
#          422 (falta nota en cambio de estado; owner_user_id ajeno al tenant;
#               body vacío -- ni estado ni owner_user_id presentes), 401 (sin auth)

TRANSICIONES_VALIDAS: dict[str, set[str]] = {
    "descubierto": {"triage", "aceptado_riesgo"},
    "triage": {"asignado", "aceptado_riesgo"},
    "asignado": {"en_progreso", "aceptado_riesgo"},
    "en_progreso": {"mitigado", "aceptado_riesgo"},
    "mitigado": {"verificado", "aceptado_riesgo"},
    "verificado": {"cerrado", "aceptado_riesgo"},
    "cerrado": set(),
    "aceptado_riesgo": set(),
}
DEFAULT_SLA_DIAS = {"critical": 7, "high": 30, "medium": 90, "low": None, "info": None}
```

```sql
CREATE TABLE IF NOT EXISTS finding_transiciones (
    id              TEXT PRIMARY KEY,
    finding_id      TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    estado_anterior TEXT,
    estado_nuevo    TEXT,
    owner_anterior  TEXT,
    owner_nuevo     TEXT,
    nota            TEXT NOT NULL DEFAULT '',
    changed_by      TEXT REFERENCES users(id) ON DELETE SET NULL,  -- NULL = sistema (drift)
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_finding_transiciones_finding_id ON finding_transiciones(finding_id);

CREATE TABLE IF NOT EXISTS tenant_sla_config (
    tenant_id     TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    dias_critical INTEGER NOT NULL DEFAULT 7,
    dias_high     INTEGER NOT NULL DEFAULT 30,
    dias_medium   INTEGER NOT NULL DEFAULT 90,
    dias_low      INTEGER  -- NULL = best-effort, sin plazo (default)
);
```

**Explícitamente fuera de este contrato** (no construir UI contra esto todavía):
no existe endpoint de lectura de `finding_transiciones` (historial) en esta
ronda — solo se persiste. Si `fase2b` necesita mostrar historial, es un
follow-up (`GET /findings/{id}/transiciones`), no cubierto aquí.

## Testing Strategy

| Layer | What to Test | Approach |
|---|---|---|
| Unit | Máquina de estados | Cada transición válida/inválida contra `TRANSICIONES_VALIDAS` |
| Integration | `PATCH /findings/{id}` | 200/404/409/422/401 por escenario, scoping por tenant |
| Integration | SLA | Insert por severidad con/sin `tenant_sla_config`, verificar `sla_due_at` |
| Integration | Drift | Dos scans del mismo asset con mismo tipo+endpoint → update no duplica; finding cerrado reaparece → vuelve a `descubierto` + fila en `finding_transiciones` |
| Regresión | Suite completa | `pytest tests/` sin romper `test_finding_detail.py`/multi-tenant existentes |

## Migration / Rollout

Aditivo puro: 2 tablas nuevas, ningún ALTER sobre tablas existentes. `git
revert` sin downtime. Findings insertados antes de este cambio no tienen
`asset_id` resoluble en su `scan_id` → quedan fuera del drift (documentado en
spec.md), no rompen nada.

## Open Questions

- [ ] ¿`GET /findings/{id}/transiciones` (historial) se necesita para el MVP
  de `fase2b`, o alcanza con ver el estado actual? Deferido — no bloquea esta
  ronda de diseño.
- [ ] Índice compuesto `(tenant_id, tipo, endpoint)` sobre `findings` para que
  el lookup de drift no sea O(n) por asset — se recomienda para `sdd-apply`,
  no especificado aquí como bloqueante.
