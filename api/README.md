# api/ — Servicio FastAPI del MVP (Fase 1)

Envuelve `orchestrator.graph` (grafo LangGraph ya construido: Recon -> gate
de autorización -> Escaneo -> Verificación -> Priorización -> Remediación ->
Reportería) detrás de dos endpoints HTTP simples. Ver sección 5 del plan
maestro (`../plan-proyecto-ciberseguridad.md`) para el alcance exacto de la
Fase 1.

## Regla de oro (repetida a propósito)

**Nunca apuntes este servicio contra un dominio/IP/app de un tercero sin una
autorización de pruebas de seguridad firmada** (`../legal/autorizacion-pruebas-seguridad.md`).
Mientras no exista ese documento firmado para un cliente real, las únicas
pruebas válidas son aplicaciones de laboratorio de código abierto: **OWASP
Juice Shop** (ver `../eval/setup_juiceshop.md` para levantarlo en
`http://localhost:3000`) y **DVWA**. El endpoint `POST /scan` acepta
`autorizacion_firmada: false` sin rechazar la petición — es el grafo
(`gate_autorizacion` en `orchestrator/graph.py`, código determinista, no un
prompt) el que bloquea el Escaneo Activo en ese caso.

## Cómo correr el servicio

Desde la raíz del repo (`D:\freestyle\ciberseguridad`), con las dependencias
de `pyproject.toml` instaladas:

```bash
# Windows: si el alias `python` no resuelve (Microsoft Store), usar `py`
py -m uvicorn api.main:app --reload
```

Por defecto queda en `http://127.0.0.1:8000`. Para probar en un puerto no
estándar (recomendado durante desarrollo, para no chocar con otros
servicios locales):

```bash
py -m uvicorn api.main:app --reload --port 8010
```

Documentación interactiva autogenerada: `http://127.0.0.1:8010/docs`.

### Variables de entorno

Copia `.env.example` (raíz del repo) a `.env` y completa `ANTHROPIC_API_KEY`
si quieres que los agentes narrativos (Orquestador, Reportería,
Priorización, Remediación, Anti-Suplantación) usen Claude de verdad. Si la
key no está configurada, cada agente cae en un fallback textual explícito
(`LLMNoDisponibleError` capturado) y el pipeline sigue corriendo sin
crashear — solo el texto narrativo queda pendiente, los datos crudos y la
puerta de autorización siguen funcionando igual.

## Endpoints

### `GET /health`

Healthcheck simple. No compila el grafo por request (se compila una sola
vez al arrancar el proceso) ni toca ninguna herramienta externa.

```bash
curl http://127.0.0.1:8010/health
```

### `POST /scan`

Dispara el pipeline completo contra `target`. Devuelve el `PipelineState`
final: hallazgos crudos, verificados, priorizados, remediaciones, el
reporte compilado y el `trace_log` completo (sección 8.1 del plan —
trazabilidad de cada decisión de cada agente).

Body (JSON):

```json
{
  "target": "http://localhost:3000",
  "scope": { "dominios": ["http://localhost:3000"], "apps": [], "ips": [], "notas": "OWASP Juice Shop local" },
  "autorizacion_firmada": true,
  "contexto_negocio": "Tienda de jugos ficticia, app de laboratorio OWASP para practicar pentesting.",
  "antisuplantacion_habilitado": false
}
```

Ejemplo real contra **OWASP Juice Shop** corriendo en `http://localhost:3000`
(levántalo primero siguiendo `../eval/setup_juiceshop.md`):

```bash
curl -X POST http://127.0.0.1:8010/scan \
  -H "Content-Type: application/json" \
  -d '{
    "target": "http://localhost:3000",
    "scope": {"dominios": ["http://localhost:3000"]},
    "autorizacion_firmada": true,
    "contexto_negocio": "OWASP Juice Shop — app de laboratorio, uso permitido indefinidamente"
  }'
```

Equivalente con [HTTPie](https://httpie.io/):

```bash
http POST 127.0.0.1:8010/scan \
  target=http://localhost:3000 \
  scope:='{"dominios": ["http://localhost:3000"]}' \
  autorizacion_firmada:=true \
  contexto_negocio="OWASP Juice Shop — app de laboratorio"
```

Nota: Juice Shop no requiere el documento de autorización de la Fase 0 (es
una app pública de OWASP hecha para practicar indefinidamente — ver
`../eval/setup_juiceshop.md`), pero el campo `autorizacion_firmada` de este
endpoint sigue siendo el mismo gate que se exige para cualquier target real;
para Juice Shop simplemente se envía `true` a propósito.

**Sin autorización** (`autorizacion_firmada: false`, o el campo omitido —
default `false`): el request es aceptado igual por la capa HTTP (200 OK), y
el grafo responde con `scan_findings: []`, `autorizacion_bloqueo_motivo`
explicando el bloqueo, y un `reporte_final` que abre con el aviso
correspondiente. Ningún wrapper de `tools/scan.py` se ejecuta — verificado
manualmente contra un target ficticio (ver bitácora del proyecto).

## Estructura de la respuesta (`ScanResponse`)

`target`, `autorizacion_firmada`, `autorizacion_bloqueo_motivo`,
`recon_findings`, `scan_findings`, `verified_findings`,
`prioritized_findings`, `remediations`, `antisuplantacion_findings`,
`reporte_final`, `trace_log`, `aviso_legal`.

## Qué NO hace esta capa

- No decide si el escaneo activo se ejecuta — esa decisión vive únicamente
  en `orchestrator/graph.py::gate_autorizacion` (nodo determinista, sin LLM).
- No valida reglas de negocio (scope real autorizado, vigencia del
  documento firmado, etc.) — solo la forma del JSON de entrada vía Pydantic.
- No persiste nada todavía (sin base de datos) — Fase 1 es sincrónica:
  request entra, el grafo corre completo con `.invoke()`, la respuesta sale.
