# Tasks: Monitoreo DMARC

> Gate previo (condición de arranque, no tarea): el orquestador secuencia el
> `sdd-apply` de este change respecto a `fase3a`/`fase3b`/`fase4`+ porque
> `api/main.py`, `frontend/src/api.ts`, `frontend/src/pages/Dashboard.tsx` y
> `pyproject.toml` son compartidos. Este change solo AÑADE en ellos.

## Phase 1: Dependencias

- [ ] 1.1 En `pyproject.toml`, añadir `defusedxml` y `python-multipart` a
      `[project].dependencies` (aditivo, sin reordenar lo existente).
- [ ] 1.2 Instalar en el entorno (`pip install -e .` o equivalente) y
      confirmar import de `defusedxml.ElementTree`.

## Phase 2: Parser (tools/dmarc.py — archivo propio)

- [ ] 2.1 Crear `tools/dmarc.py` con `DmarcParseError` y
      `_decompress(data: bytes) -> bytes` (detecta gz por magic `1f 8b`, zip
      por `PK`; xml plano pasa directo).
- [ ] 2.2 `parse_aggregate_report(data: bytes) -> dict`: `_decompress`, parsear
      con `defusedxml.ElementTree`, validar raíz `<feedback>` (si no, lanzar
      `DmarcParseError`), extraer `report_metadata`/`policy_published` y una
      lista de `records` (`source_ip`, `count`, `disposition`,
      `spf_aligned`, `dkim_aligned`, `autorizado`). Rango epoch → ISO UTC.
- [ ] 2.3 `aggregate_senders(reports: list[dict]) -> list[dict]`: agrupa por
      `source_ip` sobre el `raw_json` de varios reportes; `autorizado=False`
      si falla alineación DMARC (ni SPF ni DKIM alineados). Ordena por
      mensajes desc.
- [ ] 2.4 Docstrings estilo del módulo `tools/` (qué hace, por qué defusedxml,
      qué campos son tolerantes a ausencia).

## Phase 3: API (api/main.py — añadidos aditivos)

- [ ] 3.1 `import` de `tools.dmarc` y de `UploadFile, File` de fastapi.
- [ ] 3.2 Modelos `DmarcIngestOut`, `DmarcReportOut`, `DmarcSenderOut`
      (pydantic) según los contratos de `design.md`.
- [ ] 3.3 `POST /dmarc/reports` (`UploadFile`, `Depends(get_current_user)`):
      leer bytes, `parse_aggregate_report`, en error → 422; si válido,
      `INSERT` en `dmarc_reports` con `uuid4()`, `tenant_id`, `dominio`,
      rango y `raw_json`; devolver `DmarcIngestOut`.
- [ ] 3.4 `GET /dmarc/reports`: `SELECT ... WHERE tenant_id=? ORDER BY
      created_at DESC`, `n_records` derivado de `raw_json`.
- [ ] 3.5 `GET /dmarc/summary` (query opcional `dominio`): traer `raw_json`
      del tenant, `aggregate_senders`, devolver `list[DmarcSenderOut]`.

## Phase 4: Frontend

- [ ] 4.1 En `frontend/src/api.ts`: tipos `DmarcReport`, `DmarcSender`,
      `DmarcIngestResult`; método `uploadDmarcReport(file)` con `FormData` +
      `getToken()` (sin `Content-Type` manual); `getDmarcReports()`,
      `getDmarcSummary(dominio?)`.
- [ ] 4.2 Crear `frontend/src/components/DmarcPanel.tsx`: input file + botón
      subir, lista de reportes, tabla de remitentes con las no autorizadas
      resaltadas (mismo estilo de card que `KpiWidget`/`RemediationBoard`).
- [ ] 4.3 Montar `<DmarcPanel/>` en `frontend/src/pages/Dashboard.tsx`
      (bloque aditivo, junto a los otros paneles).

## Phase 5: Testing (tests/test_dmarc.py — archivo propio)

- [ ] 5.1 Fixture: un XML RUA mínimo válido (1-2 records) + su variante gzip.
- [ ] 5.2 `parse_aggregate_report`: XML válido → metadatos + records; variante
      gzip da el mismo resultado.
- [ ] 5.3 XXE / entidad recursiva → `DmarcParseError`, sin leer archivos ni
      colgarse (scenario de spec).
- [ ] 5.4 XML sin `<feedback>` → `DmarcParseError` (no persiste fila vacía).
- [ ] 5.5 `aggregate_senders`: IP con SPF+DKIM fail → `autorizado=false`; IP
      con DKIM pass alineado → `autorizado=true`.
- [ ] 5.6 Integración (`client` fixture): `POST /dmarc/reports` persiste fila
      scoped al tenant; archivo inválido → 422.
- [ ] 5.7 Integración cross-tenant: reportes del tenant B no aparecen en
      `GET /dmarc/reports` ni `/summary` del tenant A (patrón
      `test_finding_detail.py`).
- [ ] 5.8 Correr `pytest tests/` (verde, sin romper baseline) y frontend
      `npm run build` (`tsc -b && vite build`) sin errores.

## Phase 6: Cierre

- [ ] 6.1 Documentar en el docstring de `POST /dmarc/reports` la vía de
      ingesta (upload manual v1) y la evolución futura (buzón/poller).
- [ ] 6.2 Actualizar `state.yaml` (`apply: complete`) y notas con desviaciones
      reales del design (p. ej. namespaces de Microsoft si aparecieron).
