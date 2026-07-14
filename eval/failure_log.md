# Bitácora de fallos

Implementa el punto 6 de la seccion 8.2 del plan maestro: *"cada vez que el
sistema se equivoca (falso positivo grave o algo que no encontro y debia), se
documenta como caso de prueba nuevo que se agrega al set de evaluacion — asi
el sistema no vuelve a fallar exactamente igual dos veces."*

Cuando el pipeline (`orchestrator/agents`, aun no construido) corra contra
Juice Shop u otro objetivo de laboratorio y produzca un resultado
incorrecto (`eval/run_eval.py` lo marca como false positive o false negative),
agrega una fila nueva aqui ANTES de cerrar el caso. Si el fallo revela una
vulnerabilidad o escenario que no estaba en `eval/ground_truth.yaml` /
`eval/known_vulnerabilities.md`, agregalo tambien alli (con su `VULN-0XX`
correspondiente) para que el set de evaluacion crezca con cada fallo real.

## Como llenar una entrada

| Campo | Que va aqui |
|---|---|
| Fecha | Fecha en que se detecto el fallo (YYYY-MM-DD) |
| Caso / objetivo | Contra que corrio (ej. "Juice Shop v17.1.1 local") |
| Hallazgo esperado | Que decia la ground truth (id de `ground_truth.yaml` si aplica) |
| Hallazgo real del pipeline | Que reporto (o no reporto) el pipeline |
| Tipo de fallo | `false_positive` \| `false_negative` \| `mal_priorizado` \| `otro` |
| Causa raiz | Diagnostico de por que fallo (prompt mal redactado, herramienta no configurada, matching insuficiente, etc.) |
| Accion correctiva | Que se cambio (nueva version de prompt, ajuste de herramienta, nuevo caso agregado a ground_truth.yaml) |
| Estado | `abierto` \| `en progreso` \| `corregido y re-evaluado` |

---

## Plantilla (copiar esta fila para cada caso nuevo)

| Fecha | Caso / objetivo | Hallazgo esperado | Hallazgo real del pipeline | Tipo de fallo | Causa raíz | Acción correctiva | Estado |
|---|---|---|---|---|---|---|---|
| YYYY-MM-DD | | | | | | | abierto |

---

## Ejemplo (comentado, solo como referencia — no es un caso real todavía)

<!--
| Fecha       | Caso / objetivo         | Hallazgo esperado                          | Hallazgo real del pipeline                          | Tipo de fallo   | Causa raíz                                                                 | Acción correctiva                                                                 | Estado                  |
|-------------|--------------------------|---------------------------------------------|------------------------------------------------------|-----------------|------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|--------------------------|
| 2026-08-01  | Juice Shop v17.1.1 local | VULN-005 (broken_access_control /#/administration) | El Agente de Escaneo no reportó nada para esa ruta   | false_negative  | Nuclei no tiene plantilla para rutas SPA del lado del cliente; el chequeo requería seguir el enrutamiento Angular | Se agregó verificación específica en Agente de Recon para rutas de admin conocidas de SPAs; se subió a v2_recon.md | corregido y re-evaluado |
-->

## Entradas reales

_(vacío por ahora — se llena a medida que el pipeline corra casos reales
contra Juice Shop u otros objetivos de laboratorio)_
