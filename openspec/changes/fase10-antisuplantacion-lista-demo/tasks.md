# Tasks: Fase 10 — Antisuplantación lista para demo

Los 3 workstreams son **independientes en archivos** y corren en paralelo:
- WS1 (seed): script nuevo + datos, no toca código core.
- WS2 (eval): archivos nuevos en `eval/`, no toca código core.
- WS3 (redes): `tools/antisuplantacion.py` + `agents/antisuplantacion.py` +
  `api/main.py` builder — el único que toca código core.

El orquestador integra, corre la suite completa una sola vez, y commitea.
Ningún agente commitea ni pushea.

## WS1 — Seed de watched_brands para demo

- [ ] 1.1 Script de seed (`scripts/seed_demo_antisuplantacion.py` o similar):
  crear tenant demo + assets dominio verificados + entradas `watched_brands`,
  con 1-2 dominios públicos. Etiquetar todo como datos de demo.
- [ ] 1.2 Correr el camino real (`ejecutar_antisuplantacion` / ciclo) contra
  esas marcas y persistir hallazgos reales.
- [ ] 1.3 Verificar: `GET /antisuplantacion/dominios` de la cuenta demo
  devuelve hallazgos con defensivos excluidos y urgentes en critical.

## WS2 — Eval de regresión de antisuplantación

- [ ] 2.1 Congelar un snapshot real (`eval/baseline_real/antisup_*_snapshot.json`)
  de una corrida `run_dnstwist` real.
- [ ] 2.2 `eval/ground_truth_antisuplantacion.yaml`: etiquetar cada dominio
  (defensivo / tercero / urgente) con la señal INDEPENDIENTE que respalda la
  etiqueta. Documentar la limitación anti-circular explícitamente.
- [ ] 2.3 `eval/run_eval_antisuplantacion.py`: corre el pipeline sobre el
  snapshot, compara contra el ground truth, reporta métricas, exit != 0 si se
  rompe la separación.
- [ ] 2.4 Medir y anotar en `eval/failure_log.md`.

## WS3 — Triaje honesto de redes sociales

- [ ] 3.1 Medir sobre la salida real de Sherlock (username real) qué señales
  permiten separar el perfil oficial con confianza. Documentar el resultado
  de la medición ANTES de escribir el triaje.
- [ ] 3.2 Si hay señal confiable: cablear igual que dominios (marcar en
  `node`, filtrar/degradar en `_extraer_findings_antisuplantacion`). Si NO:
  degradar todos los perfiles a `info` ("revisión humana") y documentar.
- [ ] 3.3 Test de regresión que proteja la decisión tomada (no un test verde
  vacío).

## Cierre (orquestador)

- [ ] 4.1 Integrar los 3 workstreams, resolver conflictos.
- [ ] 4.2 Suite completa verde (`py -3 -m pytest tests/ -q`).
- [ ] 4.3 Anotar mediciones en `eval/failure_log.md`.
- [ ] 4.4 Commit + `git push privado main:producto`. NO `origin`.
