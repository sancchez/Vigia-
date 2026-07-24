# Proposal: Monitoreo DMARC (ingesta de reportes agregados + vista de remitentes no autorizados)

## Intent

SPF/DKIM/DMARC protegen el dominio EXACTO del cliente, pero no dicen nada
sobre lookalikes (eso lo cubren `fase3a`/`fase3b`). Lo que sí aportan, y hoy
Vigia no aprovecha, es **quién está enviando correo haciéndose pasar por el
dominio real del cliente**: los reportes DMARC agregados (RUA) que los
grandes receptores (Google, Microsoft, Yahoo) devuelven a diario listan cada
IP de origen y si su correo pasó o falló la autenticación del dominio. Esa es
inteligencia directa de abuso de marca por email. La tabla `dmarc_reports`
(Fase 0) existe pero nadie la escribe ni la lee. Este change cierra ese hueco
con el mínimo esfuerzo viable para un v1.

## Scope

### In Scope
- `tools/dmarc.py` (NUEVO): parser tolerante de un reporte DMARC agregado
  (XML estándar RFC 7489), con descompresión previa `.gz`/`.zip`, parseo
  **seguro** (defusedxml, la fuente es no confiable) y proyección a un
  `raw_json` normalizado (metadatos + una fila por `record`).
- `POST /dmarc/reports`: **upload manual** de un archivo de reporte
  (`.xml`/`.xml.gz`/`.zip`) que el usuario ya recibe en su propio buzón
  `rua=`. Persiste una fila en `dmarc_reports` (scoped al tenant).
- `GET /dmarc/reports`: lista de reportes ingeridos (dominio, rango, conteos).
- `GET /dmarc/summary`: la **vista** — agrega sobre los `raw_json` del tenant
  y devuelve, por IP de origen, mensajes totales y resultado DMARC
  (SPF/DKIM alineados o no), marcando como "remitente no autorizado" toda IP
  cuyo correo falla la autenticación del dominio del cliente.
- Panel frontend nuevo (`DmarcPanel.tsx`) montado en `Dashboard.tsx`.
- Tests de parser (incluye XML malicioso/XXE), tenant scoping y agregación.

### Out of Scope
- **Buzón de email dedicado + poller IMAP** para auto-ingesta: mucha más
  infra (dirección, credenciales, worker, parseo MIME) para el mismo dato.
  Se documenta como evolución futura; v1 = upload manual.
- Reportes DMARC **forensic/failure (RUF)** — solo agregados (RUA).
- Columnas/tabla SQL nuevas: `dmarc_reports` ya alcanza (raw_json + agregación
  en lectura, mismo patrón que `fase2c`). Sin migración.
- Correlacionar IPs contra `assets` o contra lookalikes de `fase3a`/`fase3b`
  (integración cruzada diferida — este change no puede ver esos diseños).
- Verificar/consultar los registros SPF/DKIM/DMARC vía DNS en vivo (el título
  del plan dice "SPF/DKIM"; el dato accionable v1 está en los reportes RUA).

## Approach

Reusar el patrón ya probado por `fase1`/`fase2c`: **parser tolerante sobre
bytes → `raw_json` → agregación en Python en lectura**, sin funciones SQL de
fecha ni columnas nuevas. Ingesta por upload (cero infra nueva); el usuario
apunta su `rua=mailto:` a su propio buzón y sube el XML. La "autorización" de
un remitente se deriva del propio veredicto DMARC del reporte (SPF **o** DKIM
alineado = autorizado), no de una lista que Vigia deba mantener.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `pyproject.toml` | Modified | Añade `defusedxml` (parseo XML no confiable) y `python-multipart` (upload). Archivo compartido con changes hermanos — ver design.md |
| `tools/dmarc.py` | New | Descompresión gz/zip, parseo seguro RUA, agregación de remitentes |
| `api/main.py` | Modified | 3 endpoints DMARC + modelos pydantic. Archivo compartido |
| `frontend/src/api.ts` | Modified | Tipos + métodos (incl. upload multipart). Archivo compartido |
| `frontend/src/components/DmarcPanel.tsx` | New | Vista de reportes + tabla de remitentes no autorizados |
| `frontend/src/pages/Dashboard.tsx` | Modified | Monta el panel. Archivo compartido |
| `tests/test_dmarc.py` | New | Parser, XXE-safety, tenant scoping, agregación |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| XML no confiable (XXE / billion-laughs) desde un reporte manipulado | Med | `defusedxml` obligatorio; nunca `xml.etree` directo sobre input externo. Test explícito con payload XXE |
| Conflicto en archivos compartidos (`api/main.py`, `Dashboard.tsx`, `api.ts`, `pyproject.toml`) con `fase3a`/`fase3b`/`fase4`+ | High si se aplica en paralelo | Todo lo del dominio DMARC vive en `tools/dmarc.py`/`DmarcPanel.tsx` (archivos propios); en los compartidos solo se AÑADEN rutas/tipos/bloques aditivos. El orquestador secuencia el apply (ver design.md) |
| Variabilidad real del XML entre receptores (namespaces, campos opcionales) | Med | Parser tolerante (`.get`/defaults), guarda `raw_json` completo aunque un campo falte; un reporte inválido → 422, no 500 |
| `python-multipart` como dependencia nueva | Low | Necesaria para `UploadFile`; alternativa (raw body) da peor UX. Aditiva, sin efecto en rutas existentes |

## Rollback Plan

Todo es aditivo: eliminar `tools/dmarc.py`, `DmarcPanel.tsx`, las 3 rutas y
modelos nuevos de `api/main.py`, los tipos/métodos de `api.ts`, el montaje en
`Dashboard.tsx`, y revertir las 2 deps de `pyproject.toml`. Sin migración de
datos que deshacer (no se tocó el schema; `dmarc_reports` ya existía vacía).

## Dependencies

- `dmarc_reports` (Fase 0, ya en `db/schema.sql`) — se consume tal cual.
- Ninguna dependencia de runtime con `fase3a`/`fase3b` (complemento, no
  consumidor). Sí comparte archivos de código con ellos y con `fase4`–`fase7`
  → el orden de `sdd-apply` lo impone el orquestador, no este proposal.

## Success Criteria

- [ ] `POST /dmarc/reports` ingiere un `.xml`/`.xml.gz`/`.zip` real y persiste
      una fila `dmarc_reports` scoped al tenant.
- [ ] `GET /dmarc/summary` lista IPs de origen con su veredicto DMARC y marca
      las no autorizadas (fallan SPF y DKIM alineación) para el dominio.
- [ ] Un XML con XXE/entidad expandible NO ejecuta ni cuelga (defusedxml),
      responde 422.
- [ ] Un reporte de otro tenant nunca aparece en `GET /dmarc/*` (404/aislado).
- [ ] Panel frontend muestra reportes + remitentes no autorizados sin romper
      `Dashboard.tsx`.
