# Proposal: Revisión de código con IA (exponer `revisar_codigo`)

## Intent

`agents/revision_ia.py::revisar_codigo()` ya está completo y probado (Semgrep
encuentra patrones sintácticos; este agente encuentra fallas de LÓGICA DE
NEGOCIO que un SAST no ve), pero NO está expuesto: su propio docstring pide un
`POST /reports/revision-ia`. Sin endpoint ni UI, la función es código muerto
desde el punto de vista del producto. Fase 4 del plan entregable cierra ese
gap: infraestructura (endpoint + UI), no razonamiento (el prompt y las
categorías ya están resueltos en open-core).

## Scope

### In Scope
- `POST /reports/revision-ia` que invoca `revisar_codigo()` con el **doble
  consentimiento** que el código ya modela: `autorizacion_firmada` (permiso de
  pruebas) + `envio_codigo_a_llm_autorizado` (permiso específico de enviar
  código a un LLM externo). Ambos re-validados en el borde de la API (403),
  además de la doble comprobación interna de la función.
- Ingesta segura de código: subida de archivos a un **sandbox por tenant**
  (`uploads/revision_ia/{tenant_id}/{review_id}/`) — nunca rutas absolutas
  arbitrarias del servidor (evita LFI/traversal en un SaaS multi-tenant).
- Persistir hallazgos como `scans`+`findings` sintéticos (precedente:
  CertStream), reusando `_registrar_findings_con_ciclo_de_vida` → entran a
  `GET /findings`, tablero (2b), KPIs (2c) y cumplimiento sin schema nuevo.
- UI: subir/seleccionar código, dos checkboxes de consentimiento separados,
  ver hallazgos de lógica de negocio.

### Out of Scope
- Tocar `agents/revision_ia.py` o `vigia_core_private/` (prompt/categorías ya
  resueltos — solo se invoca la función).
- Chunking de repos grandes (los límites `_MAX_*` existentes se respetan).
- Verificar-cierre (2c) sobre hallazgos de código: no hay endpoint HTTP
  re-escaneable — misma limitación ya documentada para SAST.
- Auto-alimentar `semgrep_findings` como contexto (hook queda, v1 pasa `None`).

## Approach

Mismo patrón que `GET /reports/cumplimiento`: función bajo demanda invocada
directo desde `api/main.py`, fuera del grafo. La única pieza nueva no trivial
es la ingesta segura (multipart → sandbox tenant-scoped con validación de que
las rutas resueltas no escapan del sandbox). Severidad `alta/media/baja` del
hallazgo se mapea a `high/medium/low` de `findings`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `api/main.py` | Modified | Modelos + `POST /reports/revision-ia`, helper de sandbox y de persistencia; agregar prefijo a la lista de rutas del catch-all SPA |
| `frontend/src/api.ts` | Modified | Tipos + método (multipart) |
| `frontend/src/components/RevisionIA.tsx` | New | UI subir/seleccionar + 2 consentimientos + hallazgos |
| `frontend/src/pages/Dashboard.tsx` | Modified | Montar el componente/tab |
| `tests/` | New | Doble consentimiento, sandbox/traversal, persistencia, mapeo de severidad |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| LFI: leer rutas arbitrarias del servidor y mandarlas a un LLM | High | Sandbox por tenant; rechazar rutas que escapen del sandbox tras `resolve()` |
| Conflicto en `api/main.py`/`Dashboard.tsx`/`api.ts` con changes hermanos (3a/3b/3e/5/6/7) | High si en paralelo | `sdd-apply` de este change corre secuenciado por el orquestador, no en paralelo; `design.md` lista archivos exactos y puntos de montaje |
| Enviar código propietario a un LLM sin consentimiento real | Med | Doble flag exigido en API y en la función; nada se lee si falta cualquiera |
| LLM alucina hallazgos | Med | Ya mitigado en el módulo (`[]` cuando no hay, prohíbe inventar citas); no se agrega verificación nueva |

## Rollback Plan

Todo aditivo: eliminar la ruta nueva y helpers de `api/main.py`, el componente
y su montaje en el frontend, y el método de `api.ts`. Sin migración de datos
(no hay tabla ni columna nueva). Borrar el directorio sandbox de uploads.

## Dependencies

- `agents/revision_ia.py` (ya en main, completo — solo se invoca).
- Ninguna dependencia de código sobre los changes hermanos; sí orden de merge
  (secuenciado por el orquestador para evitar conflictos de archivo).

## Success Criteria

- [ ] `POST /reports/revision-ia` corre `revisar_codigo()` y devuelve
      `hallazgos_logica` + metadatos, exigiendo ambos consentimientos (403 si
      falta cualquiera).
- [ ] Código sólo se lee desde el sandbox del tenant; rutas fuera del sandbox
      se rechazan.
- [ ] Hallazgos persistidos aparecen en `GET /findings` sin schema nuevo.
- [ ] UI permite subir código, marcar los 2 consentimientos y ver hallazgos.
