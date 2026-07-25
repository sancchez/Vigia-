# eval/ — Harness de evaluación continua

Esta carpeta implementa la seccion 8.2 del plan maestro
(`../plan-proyecto-ciberseguridad.md`): el **loop de evaluación y mejora
continua** que separa un prototipo que impresiona una vez de un producto que
un cliente puede pagar con confianza mes a mes.

> Alcance de esta carpeta: SOLO `eval/`. El pipeline real de agentes
> (`orchestrator/`, `agents/`, `tools/`) lo construye otro trabajo en
> paralelo. Donde este harness necesita referenciarlo, lo hace como comentario
> de import esperado (ej. `from orchestrator.agents.recon import ...`), nunca
> como import real — este harness no depende de que ese código exista todavía
> para poder correr (`eval/run_eval.py` funciona hoy mismo contra datos de
> ejemplo, ver más abajo).

## El loop completo (sección 8.2) aplicado a este proyecto

```
  1. EVALUAR  ──▶  2. MEDIR  ──▶  3. VERSIONAR  ──▶  4. REGRESIÓN  ──▶  5. DESPLEGAR
       ▲                                                                      │
       │                                                                      ▼
       └──────────────────────  7. VOLVER A EVALUAR  ◀──  6. REGISTRAR FALLOS
```

### 1. Evaluar — set de evaluación fijo

El pipeline completo (cuando exista) corre contra una aplicación de
laboratorio con vulnerabilidades ya documentadas: **OWASP Juice Shop**.

- Cómo levantar el target: `eval/setup_juiceshop.md` (opción Docker Compose
  vía `eval/docker-compose.yml`, o alternativa sin Docker con
  `git clone` + `npm install` + `npm start` — Docker no está instalado en
  esta máquina todavía, por eso ambas opciones quedan documentadas).
- Qué se sabe que tiene ese laboratorio: `eval/known_vulnerabilities.md`
  (catálogo legible) y `eval/ground_truth.yaml` (mismo catálogo, parseable
  1:1 por máquina).

### 2. Medir — precisión, recall y FP-rate

`eval/run_eval.py` calcula las métricas concretas que pide el punto 2 de la
sección 8.2:

- **Precisión** = TP / (TP + FP) — de lo que el pipeline reportó, cuánto era
  real.
- **Recall** = TP / (TP + FN) — de lo que existía en `ground_truth.yaml`,
  cuánto encontró.
- **FP-rate** = FP / (TP + FP) — Fase 7
  (`openspec/changes/fase7-confianza-calidad`): la tasa de falsos positivos
  como KPI de producto de primera clase (equivalente a `1 - precisión`,
  pero expuesto con su propio nombre porque el gate de regresión lo trata
  como métrica independiente). `--json` emite solo las métricas como JSON
  en stdout (sin el reporte de texto), para consumo por otros scripts.

El script recibe:
- la ground truth (`eval/ground_truth.yaml`, por defecto),
- una lista de hallazgos reportados en JSON (`eval/sample_findings.json` por
  defecto — placeholder hasta que el pipeline real exista; el formato exacto
  que debe producir el pipeline está documentado como docstring dentro de
  `run_eval.py`),

y hace matching por **tipo + ubicación aproximada** (no exact string match —
ver la sección "Algoritmo de matching" dentro de `run_eval.py`, porque un
escáner real nunca reporta el endpoint carácter por carácter igual a la
ground truth).

Correr:
```
python eval/run_eval.py
```
(o con rutas explícitas: `python eval/run_eval.py --ground-truth eval/ground_truth.yaml --findings eval/sample_findings.json`)

### 3. Versionar — prompts en git

Punto 3 de la sección 8.2: *"cada prompt de la sección 7 vive en git,
versionado igual que el código."*

- `eval/prompt_versions/README.md` — convención de nombres, cuándo bumpear
  versión, changelog.
- `eval/prompt_versions/v1_*.md` — los 8 prompts base extraídos literalmente
  de la sección 7 del plan maestro (orquestador, recon, escaneo, verificación,
  priorización, remediación, reportería, anti-suplantación), cada uno con
  fecha y changelog inicial.

Cuando el equipo que construye `orchestrator/agents` cambie un prompt, la
convención es: crear `v2_{agente}.md` (nunca sobreescribir `v1`), correr el
harness de evaluación con el prompt nuevo, y solo promoverlo si no empeora
las métricas (paso 4).

### 4. Regresión obligatoria antes de desplegar

Punto 4 de la sección 8.2: *"ningún cambio de prompt o de herramienta se pasa
a producción sin antes correr el set de evaluación completo. Si un cambio
baja el recall o sube los falsos positivos, no se despliega."*

**Fase 7** (`openspec/changes/fase7-confianza-calidad`) automatiza este paso,
que antes era manual: `eval/regression_runner.py` corre `run_eval.py`
in-process, compara `recall`/`fp_rate` contra `eval/baseline.json`
(versionado en git) y **sale con exit code != 0 si hay regresión**,
nombrando la métrica degradada — pensado para un CI/hook de pre-despliegue.

```
python eval/regression_runner.py                     # compara contra baseline.json, exit 0/1
python eval/regression_runner.py --update-baseline    # regenera baseline.json (paso EXPLÍCITO, revisado)
```

El baseline **nunca se reescribe solo** — actualizarlo es un paso manual e
intencional, mismo criterio que `eval/prompt_versions/README.md` ya exige
para promover un prompt nuevo. `--recall-tolerancia`/`--fp-rate-tolerancia`
(default `0.0`, ninguna degradación tolerada) permiten un margen explícito
si hace falta.

En la práctica: antes de promover cualquier `v{N}_{agente}.md` nuevo o
cualquier cambio en las herramientas de escaneo, correr
`python eval/regression_runner.py` con los hallazgos que produzca el
pipeline en esa versión. Si sale con exit != 0, no se despliega — el
archivo de versión nueva puede quedar en git como experimento, pero el
pipeline sigue apuntando a la versión anterior.

### 4b. Calidad de los agentes de razonamiento (fallback público)

Además de medir DETECCIÓN (pasos 1-4), Fase 7 agrega `eval/agent_quality/`:
evals de CALIDAD para `agents/priorizacion.py`, `agents/remediacion.py` y
`agents/revision_ia.py`, corriendo únicamente contra su modo
`fallback_publico` (`agents._llm.call_claude` mockeado con respuestas
canned — determinista, sin red, sin costo):

- Conformidad de esquema JSON de la salida de cada agente.
- Orden por severidad técnica del fallback de priorización.
- Ausencia de marcadores de sobre-promesa/alucinación (ej. remediación que
  promete un arreglo "100% infalible"; revisión de código que cita una
  línea que no existe literalmente en el archivo).

```
python eval/agent_quality/run_agent_quality.py
```

Ver `docs/open-core.md` (sección "Fase 7") para el split completo: los
evals de CONTENIDO de los prompts reales (`vigia_core_private/`) están
planeados, no ejecutados en este repo — viven en ese paquete privado, commit
aparte. Un modo LLM-as-judge contra la API real queda deliberadamente fuera
de la suite por defecto (no-determinismo/costo en CI, ver design.md de
`fase7-confianza-calidad`).

### 5. Desplegar

Fuera del alcance de esta carpeta (lo maneja el servicio FastAPI /
`orchestrator` que construye el otro agente). Este harness solo certifica
—vía el paso 4— si una versión está lista para desplegarse.

### 6. Registrar fallos

Punto 6 de la sección 8.2: *"cada vez que el sistema se equivoca (falso
positivo grave o algo que no encontró y debía), se documenta como caso de
prueba nuevo."*

- `eval/failure_log.md` — plantilla de bitácora (fecha, caso, hallazgo
  esperado, hallazgo real, causa raíz, acción correctiva, estado). Se llena
  cada vez que `eval/run_eval.py` marca un false positive o false negative
  contra una corrida real del pipeline.
- Si el fallo revela una vulnerabilidad de Juice Shop que no estaba en el
  catálogo, se agrega una entrada nueva `VULN-0XX` en
  `eval/known_vulnerabilities.md` **y** en `eval/ground_truth.yaml` en
  paralelo (deben mantenerse 1:1) — así el set de evaluación crece con cada
  fallo real y el sistema no vuelve a fallar exactamente igual dos veces.

### 7. Volver a evaluar

Con la ground truth ampliada (paso 6) y/o el prompt versionado (paso 3), se
vuelve al paso 1: correr `eval/run_eval.py` de nuevo contra el set
actualizado. El loop no tiene un "final" — corre cada vez que cambia un
prompt, una herramienta, o se descubre un fallo nuevo.

## Archivos de esta carpeta

| Archivo | Rol en el loop |
|---|---|
| `README.md` | Este archivo — explica el loop completo. |
| `docker-compose.yml` | Paso 1 — levanta Juice Shop vía Docker (para cuando esté disponible). |
| `setup_juiceshop.md` | Paso 1 — instrucciones exactas, con y sin Docker. |
| `known_vulnerabilities.md` | Paso 1/2 — catálogo legible de la ground truth. |
| `ground_truth.yaml` | Paso 1/2 — mismo catálogo, parseable por `run_eval.py`. |
| `run_eval.py` | Paso 2 — calcula precisión/recall/fp_rate contra hallazgos reportados. |
| `sample_findings.json` | Paso 2 — datos de ejemplo (placeholder hasta que el pipeline real exista). |
| `regression_runner.py` | Paso 4 (Fase 7) — gate de regresión automatizado contra `baseline.json`. |
| `baseline.json` | Paso 4 (Fase 7) — métricas de referencia versionadas en git. |
| `agent_quality/` | Paso 4b (Fase 7) — evals de calidad del fallback público (esquema/orden/no-alucinación). |
| `prompt_versions/` | Paso 3 — prompts versionados de los 8 agentes. |
| `failure_log.md` | Paso 6 — bitácora de fallos que alimenta el paso 7. |

## Estado actual / próximos pasos

- El pipeline real (`orchestrator/agents`) ya existe en este repo (fases
  0-2c aplicadas) — `run_eval.py` sigue aceptando cualquier JSON de
  hallazgos con la forma documentada, sea de `sample_findings.json` o de
  una corrida real contra Juice Shop/DVWA/WebGoat.
- Docker no está instalado en esta máquina — usar la opción B (sin Docker)
  de `eval/setup_juiceshop.md` mientras tanto.
- Fase 7 (`openspec/changes/fase7-confianza-calidad`) cerró la brecha de
  "gate de regresión manual" (ahora `regression_runner.py`) y agregó
  medición de calidad de los agentes de razonamiento en modo fallback
  (`agent_quality/`). Los evals de CONTENIDO de los prompts reales quedan
  planeados para `vigia_core_private/` (ver `docs/open-core.md`).
