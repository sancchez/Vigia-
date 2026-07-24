# Design: Revisión de código con IA

## Technical Approach

`revisar_codigo()` ya existe, prueba OK y resuelve el open-core. Este change es
solo INFRAESTRUCTURA: un endpoint bajo demanda (fuera del grafo, mismo patrón
que `GET /reports/cumplimiento`) + ingesta segura de código + UI. La única
pieza no trivial es leer código del cliente de forma segura en un SaaS
multi-tenant: se resuelve con un sandbox por tenant y validación de rutas.

## Architecture Decisions

### Decision: Ingesta por subida a sandbox, no rutas del servidor

**Choice**: `POST /reports/revision-ia` recibe archivos vía multipart; se
guardan en `uploads/revision_ia/{tenant_id}/{review_id}/` y `revisar_codigo()`
se invoca con esas rutas.
**Alternatives considered**: aceptar `codigo_paths` absolutos del body (lo que
la firma de la función sugiere).
**Rationale**: aceptar rutas arbitrarias del cliente es un LFI directo — un
usuario autenticado podría pedir leer `/etc/passwd`, la DB, o archivos de otro
tenant, y encima mandarlos a un LLM externo. El sandbox por tenant es la única
opción segura. Toda ruta se `resolve()` y se verifica que quede dentro del
sandbox del tenant antes de leerla.

### Decision: Doble consentimiento en el borde de la API (403), además de la función

**Choice**: el endpoint valida ambos flags y devuelve 403 con motivo distinto
si falta cualquiera, antes de tocar archivos; la función igual los revalida.
**Alternatives considered**: confiar solo en la doble comprobación interna de
`revisar_codigo()` (que devuelve traza de rechazo, no error).
**Rationale**: mismo espíritu que `POST /scan/activo` (403 explícito) y la
"defensa en profundidad" que el propio módulo documenta. Feedback claro al
cliente y ninguna lectura de disco si falta consentimiento.

### Decision: Persistir como `scans`+`findings` sintéticos, sin schema nuevo

**Choice**: crear una fila `scans` sintética (`target="revision-ia: <label>"`,
`autorizacion_firmada=1`, metadatos de la corrida en `reporte_final`/
`trace_log_json`) y colgar cada `hallazgo_logica` como `findings` vía
`_registrar_findings_con_ciclo_de_vida`.
**Alternatives considered**: (a) tabla nueva `revision_ia_reviews`; (b)
return-only sin persistir.
**Rationale**: precedente directo — CertStream ya crea scans sintéticos, y
`_extraer_findings_antisuplantacion` ya mapea señales no-HTTP a `findings`. Un
`tipo` es TEXT libre y `raw_json` es una bolsa flexible. Reusar findings hace
que los hallazgos entren gratis a `GET /findings`, tablero (2b), KPIs (2c) y
cumplimiento, y evita la reconciliación de `db/connection.py` que el schema
documenta como dolorosa. Una tabla nueva duplicaría toda esa maquinaria.

## Data Flow

    UI (upload + 2 consent) ──POST multipart──▶ /reports/revision-ia
                                                     │
                    sandbox uploads/{tenant}/{rev}/  │  (valida rutas)
                                                     ▼
                                           revisar_codigo(paths, flags)
                                                     │  hallazgos_logica
                                                     ▼
                    scans(sintético) + findings ◀── map sev alta→high
                                                     │
                                                     ▼
                                       Response(hallazgos + metadatos) ──▶ UI

## File Changes

Estos son los archivos EXACTOS que tocará `sdd-apply` (los changes hermanos
3a/3b/3e/5/6/7 comparten `api/main.py`, `api.ts` y `Dashboard.tsx` — el
orquestador secuencia el merge; nada de este change entra al grafo ni toca
`agents/`/`vigia_core_private/`/`db/`):

| File | Action | Description |
|------|--------|-------------|
| `api/main.py` | Modify | Modelos Pydantic (`RevisionIAOut`, item de hallazgo); `POST /reports/revision-ia` (multipart, doble 403); helper `_sandbox_revision(tenant_id, review_id, files)` con validación de traversal; helper `_persistir_revision_ia(...)` (scan sintético + findings + mapeo severidad). Sin tocar `_API_PATH_PREFIXES` (`reports` ya está) |
| `agents/revision_ia.py` | Read-only | Se invoca `revisar_codigo()` tal cual — NO se modifica |
| `frontend/src/api.ts` | Modify | Tipos `RevisionIAResult`/`HallazgoLogica` + método `revisarCodigo(files, consents, contexto)` con `FormData` |
| `frontend/src/components/RevisionIA.tsx` | Create | Input de archivos, 2 checkboxes de consentimiento separados (enviar deshabilitado hasta ambos), render de hallazgos (archivo, severidad, descripción, evidencia, recomendación) |
| `frontend/src/pages/Dashboard.tsx` | Modify | Importar y montar `<RevisionIA />` como sección nueva (layout apilado, junto a `RemediationBoard`/`KpiWidget`) |
| `tests/test_revision_ia_endpoint.py` | Create | Ver Testing Strategy |
| `.gitignore` | Modify | Ignorar `uploads/` (código subido del cliente, no versionable) |

## Interfaces / Contracts

Request: `multipart/form-data` — `files[]` (código), `autorizacion_firmada:bool`,
`envio_codigo_a_llm_autorizado:bool`, `contexto_negocio:str=""`.

Response 200 (shape estable de `revisar_codigo()` + `scan_id`):

    {
      "scan_id": "…|null",            // null si no se persistió (0 hallazgos)
      "hallazgos_logica": [ … ],
      "archivos_revisados": [ "…" ],
      "archivos_omitidos": [ "…" ],
      "contexto_semgrep_usado": false,
      "generado_en": "ISO-8601"
    }

Mapeo severidad: `alta→high`, `media→medium`, `baja→low`, otro→`info`.

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | mapeo severidad; validación de traversal (`..`, ruta fuera del sandbox) | pytest, funciones puras |
| Integration | 403 si falta cualquiera de los 2 flags; 200 + persistencia con ambos; findings visibles en `GET /findings`; 0 hallazgos → sin filas; LLM caído → 200 vacío (mock de `call_claude`) | TestClient + DB temporal, monkeypatch `agents.revision_ia.call_claude` |
| E2E | (opcional) subir código y ver hallazgos en la UI | manual/demo |

## Migration / Rollout

No migration required. Todo aditivo; sin tabla ni columna nueva. El directorio
`uploads/` se crea en runtime la primera vez.

## Open Questions

- [ ] ¿Alimentar `semgrep_findings` como contexto (leyendo findings SAST
      previos del tenant)? v1 pasa `None`; hook documentado, no implementado.
- [ ] ¿Retener el código subido tras la revisión o borrarlo al terminar?
      Propuesta v1: borrar el sandbox de la review al finalizar (minimiza
      retención de código propietario); confirmar con producto.
- [ ] Punto de montaje exacto en `Dashboard.tsx` puede moverse si un change
      hermano reordena el layout — es una sección independiente, sin acople.
