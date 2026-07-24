# Proposal: Verificación de cierre y KPIs de remediación

## Intent

Cerrar el bucle detección→remediación→confirmación (Fase 2 del plan
entregable): hoy un hallazgo puede pasar a `cerrado` (vía `fase2a`,
`PATCH /findings/{id}`) solo por juicio humano, sin una re-comprobación
técnica de que el arreglo funcionó. Y no existe ninguna métrica agregada
(MTTR, % dentro de SLA, tendencia de riesgo) para que el tenant o el
vendedor del servicio muestren progreso medible — ambos son requisitos
explícitos de "producto vendible v1", no solo demo.

## Scope

### In Scope
- `POST /findings/{id}/verificar-cierre`: re-escanea **solo el endpoint
  puntual** del hallazgo (no el asset completo) y, si ya no aparece,
  transiciona automáticamente `estado` a `verificado` → `cerrado`.
- Modo "single-target" real en `tools/zap_api.py` (hoy `ascan_start`
  hardcodea `recurse="true"`) para hallazgos de origen ZAP; reuso directo
  de `run_nuclei(endpoint)` para hallazgos de origen nuclei (ya es
  single-target).
- `GET /kpis` (o `GET /assets/{id}/kpis`): MTTR aproximado por severidad,
  % de hallazgos dentro de SLA, tendencia de riesgo por activo en el
  tiempo — solo lectura, agregando sobre `findings`.
- Widget frontend simple (no BI) mostrando estos KPIs.

### Out of Scope
- Cambios a `fase2a` (`PATCH /findings/{id}`, tabla/columnas de SLA) o
  `fase2b` (tablero) — este change los consume, no los modifica.
- Re-escaneo de verificación de cierre para hallazgos de origen
  semgrep/trivy/grype (SAST/SCA): no tienen un "endpoint" HTTP
  re-escaneable en el mismo sentido; se documenta como limitación, no se
  finge soporte.
- Historial de transiciones de estado con timestamp (tabla nueva) — ver
  Riesgo/Dependencia abierta abajo; si hace falta, es scope de `fase2a`.
- Persistencia del contenedor Docker de ZAP entre reinicios del proceso
  (mismo límite ya documentado en `tools/zap_api.py`).

## Approach

Reusar la infraestructura ya probada de escaneo (`tools/zap_api.py`,
`tools/scan.py::run_nuclei`) apuntada a un solo endpoint en vez de todo el
target, y el mismo patrón de "parser tolerante sobre `raw_json`" que ya
usa `fase1-deteccion-visible` para no requerir columnas nuevas de negocio
más allá de las que `fase2a` ya deja en `findings` (`estado`, `sla_due_at`,
`estado_verificacion`). Los KPIs se calculan con SQL de agregación directo
sobre `findings`, sin ETL ni tabla nueva — con la aproximación de MTTR
documentada explícitamente como tal.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `tools/zap_api.py` | Modified | `ascan_start(..., recurse: bool = True)`; nueva función `run_single_target_verification(...)` que corre ascan sin recurse sobre un endpoint puntual |
| `tools/scan.py` | Read-only | Reuso de `run_nuclei` tal cual (ya single-target) |
| `api/main.py` | Modified | Nuevo `POST /findings/{id}/verificar-cierre`, nuevo `GET /kpis` |
| `frontend/src/api.ts` | Modified | Tipos + métodos para los 2 endpoints nuevos |
| `frontend/src/pages/Dashboard.tsx` o componente nuevo | Modified/New | Widget de KPIs + botón "verificar arreglo" por hallazgo |
| `tests/` | New | Tests de single-target scan, transición automática, agregación de KPIs |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `fase2a` no persiste timestamp de transición a `cerrado`/`verificado` (no hay tabla de historial hoy) | High | MTTR se aproxima con `ultima_vez_visto`/`created_at` en vez de un delta exacto; documentado como aproximación en design.md y en el propio payload de `/kpis` (campo `precision: "aproximado"`) |
| `fase2a`/`fase2b` cambian el vocabulario de `estado` o el shape de `PATCH /findings/{id}` antes de que este change se aplique | Medium | Este change no se aplica (`sdd-apply`) hasta que `fase2a`/`fase2b` estén implementados y verificados — el orquestador valida compatibilidad antes de ese paso |
| ZAP `ascan` sin `recurse` puede no descubrir el endpoint si nunca fue visitado por el spider en esta sesión nueva | Medium | Documentado como pregunta abierta en design.md; mitigación: forzar un `spider` de un solo nivel sobre esa URL exacta antes del `ascan` puntual si la primera prueba en vivo lo requiere |
| Conflicto de archivos con `fase2a`/`fase2b` en `api/main.py`/`Dashboard.tsx` | High si se aplica en paralelo | Este change se aplica DESPUÉS, no en paralelo — condición explícita del orquestador |

## Rollback Plan

Ambos endpoints son aditivos (rutas nuevas, sin tocar contratos
existentes) y no agregan columnas SQL nuevas (reusan lo que `fase2a` ya
persiste en `findings`). Revertir es eliminar las 2 rutas nuevas de
`api/main.py`, la función nueva de `tools/zap_api.py` (dejando
`ascan_start` con el `recurse` opcional, que es retrocompatible por
default), y el widget de frontend — sin migración de datos que deshacer.

## Dependencies

- `fase2a-ciclo-vida-hallazgo` (backend): provee `estado`/`sla_due_at` ya
  poblados y el vocabulario de transición que `verificar-cierre` reutiliza.
  **No confirmado**: si persiste historial con timestamp de cierre.
- `fase2b-tablero-remediacion` (frontend): provee la vista de
  tablero/tabla donde el botón "verificar arreglo" probablemente se
  inserte; el widget de KPIs de este change puede vivir independiente si
  `fase2b` no expone un punto de montaje claro.
- Ambos deben estar **implementados y aplicados** antes de correr
  `sdd-apply` de este change (orden impuesto por el orquestador, no por
  este proposal).

## Success Criteria

- [ ] `POST /findings/{id}/verificar-cierre` re-escanea solo el endpoint
      del hallazgo (no el asset completo) y cierra automáticamente si no
      reaparece.
- [ ] `GET /kpis` devuelve MTTR por severidad, % dentro de SLA, y
      tendencia de riesgo por activo, con la aproximación de MTTR marcada
      explícitamente cuando aplique.
- [ ] Widget frontend muestra estos 3 KPIs sin romper `Dashboard.tsx`
      existente.
- [ ] Limitación de re-escaneo para hallazgos semgrep/trivy/grype
      documentada, no fingida.
