# Proposal: Fase 1 — Bucle de Detección visible

## Intent

Vigia ya detecta y verifica hallazgos reales, pero tres capacidades construidas
quedan invisibles para el cliente: (1) el detalle crudo del hallazgo
(`findings.raw_json` con descripción/solución/CWE) nunca se sirve, (2) el
escaneo activo (`POST /scan/activo`) inserta todo con `confirmado=0` fijo
porque nunca corre `agents/verificacion.py`, y (3) la evidencia de
Exploit-DB (`edb_id`, descripción de PoC) que sí calcula `verificacion.py`
no llega a ningún lado visible. El resultado percibido hoy es "todo sin
confirmar" cuando en realidad gran parte son hallazgos de configuración sin
CVE aplicable (legítimo) mezclados con hallazgos que sí deberían confirmarse
y no se confirman (bug real). Esta fase cierra la brecha "se detecta pero no
se ve", sin tocar el pipeline de detección en sí.

## Scope

### In Scope
- `GET /findings/{id}` (con scoping por `tenant_id`, mismo patrón que `GET /scans/{scan_id}`) que devuelve el `raw_json` completo más los campos de verificación ya calculados.
- Exportar `_verificar_un_hallazgo` de `agents/verificacion.py` como función pública reusable fuera del grafo LangGraph (sin nuevo import de `agents/_llm.py`: sigue determinista).
- Llamar esa función sobre cada alerta de `resultado.findings` (formato ZAP nativo) dentro de `_correr_escaneo_activo_en_background` (`api/main.py`), antes del `INSERT INTO findings`, y persistir `confirmado`/`estado_verificacion`/evidencia dentro de `raw_json` (no columna nueva — ver Dependencias).
- Frontend: componente de panel/modal de detalle de hallazgo (no existe hoy ninguno), disparado por click de fila en ambas tablas de `Dashboard.tsx` (hallazgos general y detalle de scan).
- Chip de verificación explicado (3 estados: `confirmado_via_exploitdb`, `no_verificable_sin_cve`, `no_confirmado_cve_sin_poc_publica`) con texto human-readable, sustituyendo el chip binario actual "confirmado/sin confirmar".
- Sección de inteligencia de explotación dentro del mismo panel (edb_id + descripción) cuando `evidencia_exploitdb` no está vacía.

### Out of Scope
- Columna SQL dedicada `estado_verificacion` en `findings` (pertenece a fase0-fundaciones-datos, corriendo en paralelo — ver Dependencias).
- Ciclo de vida del hallazgo (estados, dueño, SLA) — Fase 2.
- Verificación de hallazgos de `antisuplantacion_findings` — no tienen CVE, no aplica.
- Cambios a `agents/verificacion.py` más allá de exponer la función privada.

## Approach

Reusar `_verificar_un_hallazgo` (ya determinista, ya probada indirectamente
vía `node()`) en el punto de inserción del escaneo activo, igual que ya
corre implícitamente en el escaneo pasivo dentro del grafo. Como el shape de
alerta ZAP (`get_alerts()`) es un dict plano con `description`/`solution`/
`reference`/`cweid` a nivel superior (no envuelto en `{"raw": ...}` como en
el flujo de escaneo pasivo), y `_verificar_un_hallazgo` ya hace
`raw = hallazgo.get("raw", hallazgo)` con fallback al dict mismo, no hace
falta adaptar el shape — se llama directo. El resultado (dict con
`confirmado`, `estado_verificacion`, `cves_detectados`, `evidencia_exploitdb`)
se persiste completo dentro de `raw_json` (ya es el campo que se guarda hoy),
y se usa también para poblar `findings.confirmado` (columna que sí existe).
`GET /findings/{id}` simplemente deserializa `raw_json` y expone esos campos
ya calculados — cero lógica de negocio nueva en el endpoint.

El frontend agrega un tipo `FindingDetail` (superset de `Finding`) y un
componente de panel simple (overlay + tarjeta, mismo lenguaje visual que
`chip`/`var(--border)`/`var(--text-secondary)` ya usado en `Dashboard.tsx`),
sin librería de modal nueva.

## Affected Areas

| Area | Impact | Description |
|------|--------|--------------|
| `api/main.py` | Modified | Nuevo `GET /findings/{id}`; `_correr_escaneo_activo_en_background` corre verificación antes de insertar |
| `agents/verificacion.py` | Modified | `_verificar_un_hallazgo` → `verificar_un_hallazgo` (pública), sin cambio de lógica |
| `frontend/src/api.ts` | Modified | Nuevo tipo `FindingDetail`, nuevo método `getFinding(id)` |
| `frontend/src/pages/Dashboard.tsx` | Modified | onClick de fila en ambas tablas, estado de panel abierto/cerrado |
| `frontend/src/components/FindingDetailPanel.tsx` (nuevo) | New | Panel de detalle: descripción, solución, CWE, chip de verificación explicado, evidencia Exploit-DB |
| `frontend/src/index.css` | Modified | Estilos del overlay/panel, clases de chip nuevas si el estado de verificación no mapea a las existentes |
| `tests/` | New | Tests de `GET /findings/{id}` (scoping por tenant, 404), de verificación aplicada en escaneo activo |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `resultado.findings` (alertas ZAP) no tiene CVEs casi nunca en `description`/`reference` → la mayoría seguirá `no_verificable_sin_cve` | High (esperado) | Es el comportamiento correcto, no un bug — de ahí el chip explicado en vez de "arreglar" para que salga más confirmado |
| Frontend sin componente de modal previo → riesgo de inconsistencia visual | Low | Reusar variables CSS y clase `chip` existentes, revisar en design.md antes de codear |
| Exponer `raw_json` completo vía API podría filtrar campos no pensados para el cliente (ej. rutas de archivo internas de ZAP) | Low | `GET /findings/{id}` sólo expone campos curados (descripción, solución, referencia, CWE, verificación, evidencia), no el `raw_json` crudo tal cual a nivel de contrato HTTP |

## Rollback Plan

Todo el cambio es aditivo: nuevo endpoint, nueva función pública (wrapper
sobre la privada, cero cambio de comportamiento en `node()`), nuevo
componente frontend. Revertir es un `git revert` del commit de esta fase sin
downtime — no hay migración de datos ni cambio de schema. Si la verificación
en escaneo activo introduce latencia inaceptable (poco probable: es lookup
CSV local), se puede desactivar con un flag sin revertir el resto.

## Dependencies

- **Cruzada, no bloqueante**: fase0-fundaciones-datos agrega
  `findings.estado_verificacion` como columna SQL dedicada. Esta fase 1 NO
  depende de esa columna (lee/escribe todo dentro de `raw_json` existente),
  pero si esa columna llega primero, `GET /findings/{id}` podría preferir
  leerla directo en vez de parsear `raw_json` — dejar anotado como
  seguimiento, no como bloqueo.
- Ninguna dependencia externa nueva (Exploit-DB/CSV ya vendorizado, ZAP ya
  integrado).

## Success Criteria

- [ ] `GET /findings/{id}` devuelve 200 con detalle curado para un finding del propio tenant, 404 para uno de otro tenant o inexistente.
- [ ] Un escaneo activo real contra un target de prueba persiste al menos un finding con `estado_verificacion` distinto de vacío (no siempre `no_verificable_sin_cve` si hay CVE real detectable).
- [ ] Click en una fila de hallazgo en ambas tablas del Dashboard abre el panel con descripción, solución, chip de verificación explicado y evidencia Exploit-DB cuando aplica.
- [ ] Suite `pytest tests/` sigue verde.
