# Design: Fase 3a — Anti-suplantación bajo demanda + scoring de dominios clon

## Technical Approach

Un endpoint dedicado `POST /antisuplantacion` que corre SOLO la rama
anti-suplantación (no el pipeline técnico completo), seguido de un paso
determinista de enriquecimiento (scoring 0-4 + screenshot) antes de reusar
`_registrar_findings_con_ciclo_de_vida` (Fase 2a). Los dominios clon YA se
modelan como `findings` (`tipo=dominio_variante`/`perfil_red_social`, vía
`_extraer_findings_antisuplantacion`), así que heredan estado/SLA/drift/
`finding_transiciones` gratis — y por eso el TTTD sale directo del mismo patrón
MTTR de Fase 2c. Cero columnas/tablas nuevas: score, desglose, screenshot y
`ssl_not_before` viajan dentro de `findings.raw_json`. Dos endpoints de lectura
nuevos (`GET /antisuplantacion/dominios`, `GET /antisuplantacion/kpis`) más una
ruta que sirve el screenshot binario. Frontend: componente `BrandProtectionPanel.tsx`
extraído (mismo precedente que `RemediationBoard.tsx`), montado en `Dashboard.tsx`.

## Open-core routing (DECISIÓN — lectura obligatoria)

**Todo este change es PÚBLICO. NO toca `vigia_core_private/`.** Justificación por
pieza, siguiendo la tabla de `docs/open-core.md`:

| Pieza | Público/Privado | Por qué |
|---|---|---|
| Probes DNS/MX/SSL/HTTP + wrapper screenshot (`tools/antisuplantacion.py`) | **Público** | `docs/open-core.md` ya clasificó `tools/antisuplantacion.py` como wrapper delgado sin tuning propio. Estos probes son lo mismo: consultas estándar (socket/ssl/http.client/dnspython/playwright), sin umbrales propietarios. Es integración, no reasoning. |
| **Scoring 0-4** (`api/main.py` helper) | **Público** | Es una **cuenta transparente**: `score = sum([tiene_A, tiene_MX, tiene_SSL, tiene_HTTP])`, rango 0-4, sin pesos, sin umbrales calibrados, sin fórmula diseñada a medida. Cualquiera puede reimplementarla leyendo esta línea. NO califica como IP diferenciada — al contrario, es exactamente el tipo de "así te puntuamos, revísalo tú mismo" que la estrategia de transparencia quiere mostrar (mismo criterio que `tools/scan.py`). |
| **Borrador de takedown** | **Público** | Lo genera el `SYSTEM_PROMPT` de `agents/antisuplantacion.py`, que `docs/open-core.md` ya dejó público (prompt genérico, no afinado como el de remediación/priorización). Este change NO modifica ese prompt: reusa el `analisis_claude` que ya produce. |
| Endpoints, modelos pydantic, UI, KPIs (MTTD/TTTD) | **Público** | Infraestructura de producto y agregación aritmética — misma categoría que `api/*`, `db/*` y el `GET /kpis` de Fase 2c, todos públicos. |

**Seam privado documentado (NO se construye en este change).** Si una sesión
futura decide diferenciar la calidad, la ruta ya está clara y sigue el patrón de
fallback existente (`try: from vigia_core_private... except ImportError`):
(a) **pesos de scoring** — si el 0-4 plano se reemplaza por una fórmula ponderada
calibrada (ej. SSL emitido hace <7 días pesa más, MX + página de login pesa
crítico), esos pesos importarían de `vigia_core_private.antisuplantacion`;
(b) **prompt de takedown afinado** — mismo fallback que `remediacion.py`. Mientras
el scoring sea la cuenta 0-4, queda público. Este seam se menciona aquí para que
quien lo afine sepa dónde va, no porque haga falta ahora.

## Architecture Decisions

| Decisión | Elegido | Alternativas | Rationale |
|---|---|---|---|
| Modelo de datos de dominios clon | `findings.tipo='dominio_variante'` (ya es el caso) | Tabla nueva `dominios_clon` | Reusa lifecycle/SLA/drift/`finding_transiciones` de Fase 2a → TTTD gratis, cero tenant-scoping nuevo, cero migración. Una tabla aparte duplicaría toda esa maquinaria. |
| Trigger | `POST /antisuplantacion` dedicado | Flag `antisuplantacion_habilitado=true` en `POST /scan` | El flag ya existe pero acopla la protección de marca al pipeline técnico completo (recon/escaneo/verificación). Un endpoint propio permite dispararla sola, con su gate propio (asset verificado, sin autorización de escaneo activo) y correr el enriquecimiento después del nodo. |
| Ejecución del nodo | Llamar `agents/antisuplantacion.node(state)` directo con estado mínimo | Compilar y correr el grafo completo | Solo se necesita esa rama; correr recon/escaneo/verificación sería caro e irrelevante. |
| Almacenamiento de score/screenshot/SSL | Dentro de `findings.raw_json` | Columnas nuevas en `findings` | Datos específicos de impersonation (null para el 99% de findings técnicos). `raw_json` ya carga `señal`/`analisis_claude`. Volumen bajo → parsear en Python en los 2 GET es barato. Mantiene el change sin migración (menor riesgo de colisión con fases paralelas). |
| Cálculo de KPIs | Python sobre timestamps (`_parse_dt_utc_naive`) | `julianday()`/`EXTRACT(EPOCH)` en SQL | Portable SQLite/Postgres sin ramificar por motor — idéntico al `GET /kpis` de Fase 2c. |
| MTTD reference | SSL `NotBefore` del cert del clon (best-effort) | Fecha de registro WHOIS | WHOIS es frágil/rate-limited y a menudo privado; el SSL handshake ya ocurre en el scoring, así que `NotBefore` es evidencia real y gratis. Precisión declarada `aproximado`; clones sin SSL se excluyen del promedio. |
| Screenshot | Wrapper Playwright con `ToolNotInstalledError` graceful | Servicio externo; obligatorio | Mismo patrón de degradación que `check_certthreat`/`check_phishing_catcher`. La feature (score+KPIs) funciona sin navegador; el screenshot es enriquecimiento best-effort. Nunca sale data del tenant a terceros. |

## Data Flow

```
POST /antisuplantacion { asset_id }
   │ 1. SELECT asset WHERE id=? AND tenant_id=?   → 404 si ajeno, 403 si no verificado
   │ 2. INSERT INTO scans (asset_id, target, tenant_id, ...)   → scan_id real
   │ 3. estado = {target: asset.valor, antisuplantacion_habilitado: True}
   │    señales = agents.antisuplantacion.node(estado)["antisuplantacion_findings"]
   │ 4. _enriquecer_dominios_clon(señales):
   │       por cada dominio_variante:
   │         scoring = puntuar_infra_lookalike(dominio)   # tools/antisuplantacion.py
   │            → {dns_a, mx, ssl, http, ssl_not_before, score}
   │         screenshot_path = capturar_screenshot(dominio)  # best-effort, o None
   │         inyecta scoring+screenshot en raw_json de la señal
   │ 5. filas = _extraer_findings_antisuplantacion(target, señales)   # ya filtra *original
   │ 6. _registrar_findings_con_ciclo_de_vida(conn, tenant, scan_id, asset_id, filas, now)
   └─ 200 { scan_id, dominios: [{endpoint, score, estado, ...}] }

GET /antisuplantacion/dominios   → SELECT findings tipo IN (...) WHERE tenant_id=?  → parsea raw_json
GET /antisuplantacion/kpis       → mismo SELECT + finding_transiciones → TTTD/MTTD en Python
GET /antisuplantacion/dominios/{finding_id}/screenshot → sirve el archivo (tenant-scoped, 404 si ajeno)
```

## Interfaces / Contracts

```python
# tools/antisuplantacion.py  (PÚBLICO)
@dataclass
class InfraScore:
    dns_a: bool; mx: bool; ssl: bool; http: bool
    ssl_not_before: str | None   # ISO, para MTTD
    score: int                    # sum de los 4 booleanos, 0-4
def puntuar_infra_lookalike(dominio: str, timeout: int = 10) -> InfraScore: ...
def capturar_screenshot(url: str, out_dir: str, timeout: int = 20) -> str | None:  # path o None (graceful)

# api/main.py  (PÚBLICO)
class DominioClonOut(BaseModel):
    finding_id: str; endpoint: str; severidad: str; estado: str
    score: int; scoring: dict            # {dns_a, mx, ssl, http}
    screenshot: Optional[str] = None     # URL relativa a la ruta de screenshot, o None
    takedown: Optional[str] = None       # analisis_claude (borrador copiable)
    primera_vez_visto: Optional[str] = None

class AntisuplantacionRunOut(BaseModel):
    scan_id: str; dominios: list[DominioClonOut]; perfiles: list[dict]

class AntisuplantacionKpiOut(BaseModel):
    tttd_dias: Optional[float] = None; tttd_precision: str = "exacto"
    mttd_dias: Optional[float] = None; mttd_precision: str = "aproximado"
    total_clones: int; clones_activos: int   # score >= 2, informativo
```

```typescript
// frontend/src/api.ts  (PÚBLICO)
export type DominioClon = { finding_id: string; endpoint: string; severidad: string;
  estado: string; score: number; scoring: Record<string, boolean>;
  screenshot: string | null; takedown: string | null; primera_vez_visto: string | null };
export type AntisuplantacionKpis = { tttd_dias: number | null; tttd_precision: string;
  mttd_dias: number | null; mttd_precision: string; total_clones: number; clones_activos: number };
// api.runAntisuplantacion(assetId), api.getDominiosClon(), api.getAntisuplantacionKpis(),
// api.screenshotUrl(findingId)
```

## File Changes — SCOPE EXACTO que tocará `sdd-apply`

**Este es el conjunto completo de archivos que este change modifica.** Ningún
otro. Sirve para que el orquestador decida paralelo/secuencial con
`fase3b`/`fase3e`/`fase4`/`fase5`/`fase6`/`fase7`.

| File | Action | Descripción |
|---|---|---|
| `tools/antisuplantacion.py` | **Modify** | `InfraScore`, `puntuar_infra_lookalike()`, `capturar_screenshot()`. Aditivo, no toca funciones existentes. |
| `api/main.py` | **Modify** | 3 endpoints + ruta screenshot; `_enriquecer_dominios_clon()`; modelos `DominioClonOut`/`AntisuplantacionRunOut`/`AntisuplantacionKpiOut`. **Reusa** `_extraer_findings_antisuplantacion`, `_registrar_findings_con_ciclo_de_vida`, `_parse_dt_utc_naive`, `agents.antisuplantacion.node` (no los modifica). |
| `frontend/src/api.ts` | **Modify** | Tipos `DominioClon`/`AntisuplantacionKpis` + 4 funciones. Aditivo. |
| `frontend/src/components/BrandProtectionPanel.tsx` | **Create** | Sección "Protección de marca": tabla de clones (score badge, screenshot, estado), perfiles sociales, takedown copiable, tiles MTTD/TTTD. |
| `frontend/src/pages/Dashboard.tsx` | **Modify** | Botón "Proteger marca" por asset dominio verificado + `<BrandProtectionPanel/>`; fetch en `cargarTodo()`. |
| `frontend/src/index.css` | **Modify** | Clases `.chip.score-*` (0-1 bajo, 2-3 medio, 4 alto), reusando el patrón de `.chip.sla-*`. |
| `tests/test_antisuplantacion_scoring.py` | **Create** | `puntuar_infra_lookalike` (0-4, degradación por probe faltante); `capturar_screenshot` devuelve None sin navegador. |
| `tests/test_antisuplantacion_endpoint.py` | **Create** | `POST` gate (403 no verificado, 404 cross-tenant); `GET dominios` tenant-scoping; `GET kpis` TTTD exacto/MTTD null/vacío; screenshot 404 cross-tenant. Mock del nodo y de los probes. |

**Riesgo de colisión declarado**: `api/main.py`, `frontend/src/api.ts`,
`frontend/src/pages/Dashboard.tsx` e `index.css` son puntos de contacto probables
con otras fases (todas suman endpoints/UI ahí). Todo lo de este change es
**aditivo** (endpoints nuevos, tipos nuevos, un bloque nuevo en el JSX, clases CSS
nuevas) — sin renombrar ni reescribir símbolos existentes, así que un merge o un
apply secuencial resuelve sin conflicto semántico. Si el orquestador corre en
paralelo, esperar conflictos textuales solo en estos 4 archivos compartidos.

## Testing Strategy

| Layer | Qué | Cómo |
|---|---|---|
| Unit | Fórmula de scoring 0-4 y degradación | pytest con probes mockeados (cada combinación de señales) |
| Unit | `capturar_screenshot` sin navegador → None | pytest, simular ImportError/ToolNotInstalledError |
| Unit | TTTD/MTTD en Python | pytest sobre `finding_transiciones` + `raw_json` fixtures, incl. promedio que excluye nulos |
| Integration | `POST /antisuplantacion` gate + persistencia | pytest + conftest, nodo y probes mockeados; 403/404, findings creados con score en raw_json |
| Integration | `GET dominios`/`kpis`/screenshot scoping por tenant | mismo patrón que `test_finding_detail.py` |
| Manual/E2E | Panel muestra clones + screenshot + takedown copiable | verificación manual (sin infra de tests frontend, mismo gap que Fase 1/2b) |

## Migration / Rollout

**Sin migración de DB** (cero columnas/tablas). Endpoints/UI aditivos. Nuevas
deps opcionales (`dnspython` para MX, `playwright` para screenshot) degradan con
gracia si faltan — no son requisito de arranque. `git revert` sin downtime.

## Open Questions

- [x] ¿`POST /antisuplantacion` corre síncrono (dnstwist tarda segundos-minutos)
  o en background como `POST /scan/activo` (devuelve `scan_id`, la UI hace poll)?
  Recomendación para `sdd-apply`: **background**, reusando el patrón de
  `_correr_escaneo_activo_en_background`, para no bloquear el request. No bloquea el diseño.
  **RESUELTO 2026-08-02 — la recomendación original era la correcta.** Se
  implementó síncrono (ver tarea 2.3) porque spec.md exigía 200 con la lista
  resuelta, y con el supuesto de que "dnstwist tarda segundos". Ese supuesto
  se midió y es falso para un target real: >15 min sobre un dominio con 45
  lookalikes (el costo está en el enriquecimiento por dominio, no en
  dnstwist). Ahora es background + 202 + `GET /antisuplantacion/runs/{id}`,
  exactamente el patrón recomendado acá.
- [ ] ¿El screenshot se guarda en disco (`VIGIA_SCREENSHOT_DIR`) o como data-URI
  en `raw_json`? Recomendación: disco + ruta servida (evita inflar `raw_json`/DB).
- [ ] Umbral `clones_activos` (score ≥ 2) es informativo; confirmar el corte con
  uso real. No bloqueante.
