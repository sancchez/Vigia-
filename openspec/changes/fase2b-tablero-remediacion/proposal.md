# Proposal: Fase 2b — Tablero de remediación (frontend)

## Intent

Fase 0 ya agregó ciclo de vida al hallazgo (`findings.estado`, `owner_user_id`,
`sla_due_at`) pero hoy nada en la UI lo usa: `Dashboard.tsx` sigue mostrando
una tabla plana con solo severidad/tipo/endpoint/confirmado. Sin una vista que
muestre estado, dueño y vencimiento de SLA, y permita cambiarlos, el ciclo de
vida es un dato muerto en la base — el analista no tiene dónde triagear,
asignar ni ver qué está por vencer. Esta fase cierra esa brecha desde el
frontend, consumiendo un endpoint de escritura (`PATCH /findings/{id}`) que
otro change (`fase2a-ciclo-vida-hallazgo`) está diseñando en paralelo.

## Scope

### In Scope
- Vista de remediación en el Dashboard: tabla con filtros por `estado`,
  `owner_user_id` (dueño) y proximidad de SLA (vencido/próximo/ok) — decisión
  tabla-vs-kanban justificada en `design.md`.
- Badges de vencimiento de SLA derivados de `sla_due_at` (rojo=vencido,
  ámbar=próximo, verde=ok/sin SLA), mismo lenguaje visual que `.chip.*`.
- Selector de dueño poblado con `api.listMembers()` (ya existe, `/tenant/members`).
- Cambio de `estado`/`owner_user_id` desde la UI (dropdown/select por fila o
  en un panel), llamando `PATCH /findings/{id}`.
- Nuevo tipo y método en `frontend/src/api.ts` para ese PATCH, sobre un
  **contrato asumido** (ver Dependencies) — no confirmado contra el backend real.

### Out of Scope
- Cualquier código de `api/main.py`, `db/schema.sql`, `db/connection.py`,
  `agents/**` — pertenecen a `fase2a-ciclo-vida-hallazgo` (backend del PATCH,
  cálculo de `sla_due_at` por severidad) y `fase2c` (re-escaneo de cierre,
  detección de reincidencia).
- Cambios en `vigia_core_private/`.
- Drag-and-drop real entre columnas (evaluado y descartado en `design.md`
  salvo que el research indique que es crítico).
- KPIs de MTTR / % dentro de SLA (mencionados en el plan de fase 2 pero no
  parte de este recorte — quedan para una fase de reporting posterior).
- Cualquier cambio a `FindingDetailPanel.tsx` más allá de lo estrictamente
  necesario para exponer estado/dueño si el detalle ya abierto los necesita.

## Approach

Extender `Dashboard.tsx` con un bloque de tablero nuevo (mismo patrón `.card`
+ tabla ya usado para "Hallazgos detectados"), reutilizando filtros tipo-chip
como los de severidad (`filtroSev`) para estado y SLA, y un `<select>` simple
para dueño. Los badges de SLA son una función pura
`estadoSla(sla_due_at: string | null) -> {texto, clase}` (mismo patrón que
`nivelDeRiesgo`/`chipVerificacion` ya existentes), sin nueva dependencia. El
cambio de estado/dueño se hace inline (select en la fila) y dispara
`api.patchFinding(id, cambios)`, con optimistic-UI simple (refetch de
`listFindings()` tras éxito, igual que ya hace `cargarTodo()` después de
otras mutaciones en este archivo).

## Affected Areas

| Area | Impact | Description |
|------|--------|--------------|
| `frontend/src/api.ts` | Modified | Tipo `Finding` extendido (o `FindingListItem`) con `estado`/`owner_user_id`/`sla_due_at`; nuevo `api.patchFinding(id, cambios)`; tipo `PatchFindingBody`/`Member` ya existe |
| `frontend/src/pages/Dashboard.tsx` | Modified | Nuevo bloque "Tablero de remediación": filtros estado/dueño/SLA, tabla, selects inline de estado/dueño |
| `frontend/src/components/RemediationBoard.tsx` (nuevo, a confirmar en design.md) | New | Extraer el bloque de tablero como componente propio si el tamaño de `Dashboard.tsx` lo justifica |
| `frontend/src/index.css` | Modified | Clases nuevas para badges de SLA (`.chip.sla-vencido`, `.chip.sla-proximo`, `.chip.sla-ok` o similar) |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Contrato real de `PATCH /findings/{id}` (fase2a) difiere del asumido (`{estado?, owner_user_id?, nota?}` → finding actualizado) | Medium | Documentado explícitamente en `design.md` como contrato asumido a confirmar antes de `sdd-apply`; cambios de contrato son solo en `api.ts` (un punto de ajuste) |
| `GET /findings` (lista) hoy no expone `estado`/`owner_user_id`/`sla_due_at` — si el backend de fase2a no los agrega a ese endpoint, el tablero no tiene datos | Medium | Declarado como dependencia bloqueante explícita en `design.md`; si fase2a no lo cubre, se abre gap adicional a resolver antes de aplicar |
| `Dashboard.tsx` ya es un archivo grande (840+ líneas) — agregar más bloques inline empeora mantenibilidad | Low | Evaluar extracción a componente propio en `design.md` (ver Affected Areas) |
| Cambiar estado/dueño sin confirmación puede ser accidental (click de select) | Low | Sin modal de confirmación en este alcance — mismo nivel de fricción que otras acciones ya existentes en el dashboard (ej. "Escanear ahora" no pide confirmación) |

## Rollback Plan

Todo aditivo sobre `frontend/**`: nuevo bloque de UI, nuevo método de API,
nuevas clases CSS. `git revert` del commit de esta fase sin downtime — no hay
cambio de schema ni de contrato de lectura existente (`GET /findings` sigue
sirviendo lo mismo si fase2a no cambia su shape; si lo cambia, ese revert es
responsabilidad de fase2a). Si `PATCH /findings/{id}` no está disponible aún
al momento de un deploy parcial, los selects de estado/dueño pueden
deshabilitarse con un flag sin remover el resto del tablero (badges de SLA y
filtros siguen funcionando solo-lectura).

## Dependencies

- **Bloqueante para escritura, no para lectura**: `PATCH /findings/{id}`
  (`fase2a-ciclo-vida-hallazgo`, en diseño paralelo, sin código ni design.md
  final accesible en este momento). Este proposal asume un contrato estándar
  (ver `design.md` de este change para el detalle completo) que debe
  confirmarse contra el design.md real de fase2a antes de `sdd-apply`.
- **Bloqueante para todo el tablero**: `GET /findings` (lista) debe exponer
  `estado`, `owner_user_id`, `sla_due_at` por fila — hoy `Finding` en
  `api.ts` no los tiene. Si fase2a no extiende ese endpoint, este change
  necesita su propio ajuste de contrato de lectura (a resolver en design.md).
- Ninguna dependencia externa nueva (sin librerías, sin servicios).

## Success Criteria

- [ ] El Dashboard muestra un tablero de remediación con filtros por estado,
  dueño y proximidad de SLA, usando datos reales de `findings`.
- [ ] Cada hallazgo con `sla_due_at` muestra un badge correcto (vencido si
  `sla_due_at < now`, próximo si vence en ≤3 días — umbral a confirmar en
  design.md —, ok en otro caso).
- [ ] Cambiar el estado o el dueño de un hallazgo desde la UI persiste (vía
  `PATCH /findings/{id}`) y se refleja tras refetch.
- [ ] Cero cambios fuera de `frontend/**`.
- [ ] El contrato asumido del PATCH queda documentado y señalado como "a
  confirmar" en `design.md`.
