# Tasks: Fase 8 — Motor de detección

Método: **eval-driven**. Antes y después de CADA grupo, correr:

```bash
py -3 eval/run_eval.py --findings <archivo> --json
```

y anotar el número en `eval/failure_log.md`. Si un cambio no mueve `recall`
ni `fp_rate`, se revierte.

## Grupo 0 — Línea base y arreglos baratos (empezar acá)

- [ ] 0.1 Reproducir la medición base: `py -3 eval/run_eval.py --findings eval/baseline_real/juiceshop_2026-07-25_findings.json --json` → debe dar `recall 0.0`. Confirmar que el harness funciona antes de cambiar nada.
- [ ] 0.2 **Bug del clasificador**: `Cross-Domain Misconfiguration` → `higiene_configuracion` en `tools/clasificacion.py`. Revisar la lista completa de nombres ZAP contra la corrida real (`eval/baseline_real/`) y corregir los que caigan mal. Es el arreglo con mejor relación impacto/esfuerzo.
- [ ] 0.3 Investigar por qué Nuclei no aportó ningún hallazgo (`tools/scan.py::run_nuclei`): ¿se ejecutó? ¿con qué templates? ¿falló en silencio? Documentar el hallazgo.

## Grupo 1 — Deduplicación

- [ ] 1.1 Diseñar la clave de agrupación (propuesta: `tipo` + parámetro afectado; las URLs van como lista de evidencia dentro del hallazgo).
- [ ] 1.2 Implementar en el punto de persistencia (`_registrar_findings_con_ciclo_de_vida` en `api/main.py`), sin romper el ciclo de vida ni el drift de Fase 2a.
- [ ] 1.3 Decidir qué pasa con los 459 hallazgos históricos ya guardados (derivación en lectura vs migración) — mismo criterio que se usó para `clase`.
- [ ] 1.4 Medir: `fp_rate` debe bajar fuerte. `recall` no debería moverse.

## Grupo 2 — Crawling de SPAs

- [ ] 2.1 Revisar la configuración actual del ajax spider en `tools/zap_api.py` (tiempo de espera, profundidad, navegador). 104 alertas "Modern Web Application" indican que no está alcanzando.
- [ ] 2.2 Evaluar alimentar rutas conocidas (OpenAPI/sitemap/lista manual) al árbol de ZAP antes del escaneo activo — `access_url()` ya existe desde la validación de Fase 2c y sirve exactamente para esto.
- [ ] 2.3 Medir: ¿aumentó el número de endpoints distintos descubiertos?

## Grupo 3 — Escaneo autenticado (el que más mueve la aguja)

- [ ] 3.1 Configurar autenticación en ZAP (contexto + script de login) contra Juice Shop, con credenciales de prueba.
- [ ] 3.2 Diseñar cómo se persisten credenciales de prueba por asset para clientes reales — **ojo**: son secretos de un tercero, no pueden ir en texto plano. Decidir y documentar antes de implementar.
- [ ] 3.3 Correr escaneo activo autenticado contra Juice Shop.
- [ ] 3.4 Medir: acá es donde `recall` tiene que subir de verdad. Objetivo mínimo: detectar al menos un SQLi y un XSS del ground truth.

## Grupo 4 — Cierre

- [ ] 4.1 `recall >= 0.5` confirmado y anotado en `eval/failure_log.md`.
- [ ] 4.2 Actualizar `eval/baseline.json` con la nueva línea base para que el gate de regresión (Fase 7) proteja el avance.
- [ ] 4.3 Verificar que la suite existente sigue verde (`py -3 -m pytest tests/ -q`) — no como gate de calidad, sino para confirmar que no se rompió nada.
- [ ] 4.4 Commit + `git push privado main:producto`. **No pushear a `origin`** (público).

## Orquestación sugerida de agentes

El trabajo es secuencial por naturaleza (cada grupo se mide sobre el
resultado del anterior), así que **no conviene paralelizar en worktrees**
como se hizo con las Fases 3-7. Un agente por grupo, en orden, con el
número de `recall` como criterio de aceptación duro.

Excepción: el Grupo 0 (tareas 0.2 y 0.3) sí puede correr en paralelo con el
Grupo 1, porque tocan archivos distintos (`tools/clasificacion.py` y
`tools/scan.py` vs `api/main.py`).

Modelo: Sonnet alcanza para todo esto — es investigación técnica acotada con
un número objetivo, no diseño de arquitectura. Reservar Opus sólo si hay que
rediseñar cómo se modela un hallazgo.
