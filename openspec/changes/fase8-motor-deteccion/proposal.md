# Fase 8 — Arreglar el motor de detección

> **Leer esto primero en cualquier sesión nueva.** Es el único trabajo que
> importa ahora mismo. Todo lo demás (tablero, SLA, notificaciones, PDFs,
> pitch comercial) ya está construido y se apoya en que este número sea
> creíble. Hoy no lo es.

## 1. El problema, medido

Corrida real del 2026-07-25 contra OWASP Juice Shop local (Docker), escaneo
activo completo: spider + ajax_spider + ascan, ~20 minutos, 594 alertas ZAP,
459 hallazgos persistidos.

Evidencia cruda guardada en `eval/baseline_real/juiceshop_2026-07-25_findings.json`.

```
py -3 eval/run_eval.py --findings eval/baseline_real/juiceshop_2026-07-25_findings.json --json
{"tp": 0, "fp": 459, "fn": 11, "precision": 0.0, "recall": 0.0, "f1": 0.0, "fp_rate": 1.0}
```

**Cero aciertos. 459 falsos positivos. Las 11 vulnerabilidades reales del
ground truth, no detectadas.**

Desglose de lo que sí encontró — 459 hallazgos que en realidad son **6
problemas distintos** repetidos sobre 138 URLs:

| Cantidad | Tipo | Clase asignada | ¿Correcto? |
|---|---|---|---|
| 136 | Cross-Domain Misconfiguration | `vulnerabilidad_explotable` | **NO** — es configuración |
| 112 | Content Security Policy (CSP) Header Not Set | `higiene_configuracion` | sí |
| 104 | Modern Web Application | `informativo` | sí |
| 104 | Timestamp Disclosure - Unix | `informativo` | sí |
| 2 | User Agent Fuzzer | — | — |
| 1 | HTTP Only Site | — | — |

Severidades: 249 medium, 104 low, 106 info. **0 críticos, 0 altos.**
Confirmados contra Exploit-DB: **0**.

Juice Shop está lleno de SQLi y XSS documentados. No encontró ninguno.

## 2. Por qué los tests no lo detectaron (error de método, no de código)

La suite tenía **361 tests en verde** mientras el motor detectaba cero.
Los tests verifican la *plomería* (que los datos viajen, que los endpoints
respondan, que las migraciones sean idempotentes, que el tenant aísle) —
nunca verificaron la *capacidad de detección*.

Se construyeron 7 fases de producto encima de un motor cuya capacidad nunca
se midió. La lección operativa: **el eval es el gate, no los tests.** Un
test verde no dice nada sobre si el producto sirve; `recall` sí.

## 3. Método propuesto: eval-driven, no test-driven

No agregar más tests unitarios. El ciclo es:

```
medir (run_eval) → hipótesis → cambio → medir de nuevo → ¿subió el recall?
```

Cada iteración se justifica sólo si mueve `recall` o baja `fp_rate`. Un
cambio que no mueve ninguno de los dos números se descarta, por elegante que
parezca.

`eval/run_eval.py` ya existe y funciona (precision/recall/F1 contra
`eval/ground_truth.yaml`, 11 vulnerabilidades de Juice Shop con id estable
VULN-001..011). `eval/regression_runner.py` + `eval/baseline.json` (Fase 7)
automatizan el gate.

## 4. Las 5 causas identificadas, en orden de impacto

### 4.1 Escaneo autenticado (el más importante)
Las vulnerabilidades reales viven detrás del login. Hoy se escanea el
vestíbulo, no el edificio. 8 de las 11 del ground truth requieren sesión
(`/rest/basket/{id}`, `/#/administration`, JWT, reseñas…).
ZAP soporta autenticación por contexto/script — hay que configurarla y
persistir credenciales de prueba por asset.

### 4.2 Crawling de SPAs
104 alertas "Modern Web Application" son ZAP avisando literalmente que es
una app JavaScript y su spider clásico no ve las rutas. Juice Shop es
Angular; los clientes pyme reales van a ser React/Vue/Angular. El ajax
spider corrió pero no alcanzó — revisar configuración, tiempo de espera y
si conviene alimentar rutas conocidas desde un archivo OpenAPI/sitemap.

### 4.3 Deduplicación
6 problemas ≠ 459 hallazgos. Agrupar por (tipo, parámetro) y listar las URLs
afectadas como evidencia dentro de un solo hallazgo. Esto solo cambia la
percepción del producto de "ruidoso y roto" a "ordenado".

### 4.4 Bug del clasificador
`Cross-Domain Misconfiguration` se clasifica como `vulnerabilidad_explotable`
en `tools/clasificacion.py`. Son 136 hallazgos: el panel diría "136
vulnerabilidades explotables" cuando hay cero. Debe ser
`higiene_configuracion`. Revisar también qué otros nombres caen mal.

### 4.5 Nuclei no aportó nada
Los 6 tipos encontrados son todos de ZAP. Verificar si `run_nuclei` se
ejecutó, con qué templates, y por qué no produjo hallazgos.

## 5. Criterio de aceptación

Umbral mínimo para considerar el motor vendible:

- **`recall >= 0.5`** contra el ground truth de Juice Shop (detectar al menos
  6 de las 11 vulnerabilidades reales, incluyendo al menos un SQLi y un XSS).
- **`fp_rate` sustancialmente por debajo de 1.0** tras deduplicación.
- Al menos un hallazgo `confirmado` contra Exploit-DB, o explicación
  documentada de por qué ninguno aplica.
- El número de hallazgos mostrados debe ser del orden de los problemas
  reales, no de las URLs afectadas.

No hace falta llegar a recall 1.0. Hace falta que cuando el sistema diga
"encontramos esto", sea verdad, y que cuando no encuentre nada, sea porque
no hay nada obvio — no porque no supo mirar.

## 6. Alcance explícito de lo que NO entra

- Nada de producto nuevo (tablero, notificaciones, billing, UI).
- No agregar tests unitarios salvo que protejan una regresión real medida.
- No tocar `vigia_core_private/`.
- Fase 6b (billing Wompi) sigue en pausa (`HANDOFF.md:161`).

## 7. Nota de repos

`main` se pushea a `privado/producto` (repo privado `vigIA-Privado`), NO al
público. El público (`origin`, `Vigia-`) se quedó a propósito con el motor de
escaneo previo. Ver `docs/open-core.md` y la decisión del 2026-07-24.
