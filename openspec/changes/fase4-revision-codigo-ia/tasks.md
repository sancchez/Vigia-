# Tasks: Revisión de código con IA

## Phase 1: Backend — ingesta segura y persistencia

- [x] 1.1 En `api/main.py`, agregar helper `_sandbox_revision(tenant_id, review_id, files)`:
      crea `uploads/revision_ia/{tenant_id}/{review_id}/`, guarda cada archivo
      subido, `resolve()` cada ruta y rechaza (422) las que escapen del sandbox.
- [x] 1.2 Agregar helper `_map_severidad_revision(sev)`: `alta→high`, `media→medium`,
      `baja→low`, otro→`info`.
- [x] 1.3 Agregar helper `_persistir_revision_ia(conn, tenant_id, resultado, label)`:
      inserta una fila `scans` sintética (`target="revision-ia: {label}"`,
      `autorizacion_firmada=1`, metadatos en `reporte_final`/`trace_log_json`) y
      cuelga cada `hallazgo_logica` como finding vía `_registrar_findings_con_ciclo_de_vida`
      (`endpoint=f"{archivo}:{linea_aprox}"`, `raw_json`=hallazgo completo). Devuelve `scan_id` o `None` si 0 hallazgos.

## Phase 2: Backend — endpoint

- [x] 2.1 Agregar modelos Pydantic `HallazgoLogicaOut` y `RevisionIAOut` (shape de
      la sección Interfaces del design + `scan_id`).
- [x] 2.2 Agregar `POST /reports/revision-ia` (multipart) con `Depends(get_current_user)`:
      leer flags de consentimiento y archivos; devolver 403 si `autorizacion_firmada`
      es false; 403 (motivo distinto) si `envio_codigo_a_llm_autorizado` es false.
- [x] 2.3 En el handler: sandboxear archivos (1.1), invocar
      `revisar_codigo(paths, True, True, semgrep_findings=None, contexto_negocio=...)`,
      persistir (1.3), borrar el sandbox de la review, devolver `RevisionIAOut`.
- [x] 2.4 Confirmar que `reports` ya está en `_API_PATH_PREFIXES` (lo está) —
      no modificar esa lista.

## Phase 3: Frontend — UI y wiring

- [x] 3.1 En `frontend/src/api.ts`, agregar tipos `HallazgoLogica` y `RevisionIAResult`
      y el método `revisarCodigo(files, autorizacion, envioLLM, contexto)` usando `FormData`.
- [x] 3.2 Crear `frontend/src/components/RevisionIA.tsx`: input de archivos, dos
      checkboxes de consentimiento separados, botón de enviar deshabilitado hasta
      que ambos estén marcados, y render de hallazgos (archivo, severidad,
      descripción, evidencia, recomendación). Manejar 403 y estado vacío.
- [x] 3.3 En `frontend/src/pages/Dashboard.tsx`, importar y montar `<RevisionIA />`
      como sección independiente (layout apilado, junto a `RemediationBoard`).

## Phase 4: Tests

- [x] 4.1 `tests/test_revision_ia_endpoint.py`: 403 cuando falta `autorizacion_firmada`;
      403 (motivo distinto) cuando falta `envio_codigo_a_llm_autorizado`; ningún
      archivo leído en ambos casos.
- [x] 4.2 Test: con ambos flags y `call_claude` mockeado devolviendo 2 hallazgos,
      responde 200, crea `scans`+2 `findings`, y aparecen en `GET /findings`.
- [x] 4.3 Test: 0 hallazgos → sin filas `findings`, `scan_id=null`; y `call_claude`
      lanzando `LLMNoDisponibleError` → 200 con `hallazgos_logica` vacío.
- [x] 4.4 Test unit de traversal: ruta con `..`/fuera del sandbox → 422 sin lectura;
      y test de `_map_severidad_revision`.

## Phase 5: Cleanup

- [x] 5.1 Agregar `uploads/` a `.gitignore` (código subido del cliente, no versionable).
- [x] 5.2 Correr `pytest tests/` (verde) y `tsc -b && vite build` (sin errores).
