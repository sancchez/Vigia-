# Fase 10 — Antisuplantación lista para demo

> Cierra los 3 pendientes que separan "el motor de antisuplantación funciona"
> (probado end-to-end en `a05b133`) de "el producto se puede mostrar a un
> cliente". Método idéntico al resto del proyecto: **medir contra la realidad,
> no confiar en tests verdes**. Nada se marca como resuelto sin una medición
> real que lo respalde.

## Contexto (medido, no asumido)

`a05b133` dejó el filtro de defensivos cableado en el camino de producción y
verificado end-to-end (contra el dominio de una marca bancaria de un tercero (no publicada): 46 dominios → 0 defensivos
colados, 7 urgentes a critical, 28 terceros high). Lo que falta para demo:

1. **`watched_brands` está vacío** — la capa está encendida pero no vigila
   nada. Una demo necesita marcas configuradas con hallazgos reales visibles.
2. **No existe eval de regresión para antisuplantación** — la capacidad está
   medida una vez, pero nada la protege de una regresión silenciosa (como ya
   pasó con `triar_lookalikes`, que tenía tests verdes y estaba desconectado).
3. **La capa de redes sociales (Sherlock) no tiene triaje** — en la corrida
   real devolvió 44 perfiles con el nombre del cliente, sin separar oficiales
   de suplantadores de no-relacionados. Es el mismo riesgo de falso positivo
   que se arregló para dominios, un nivel más arriba.

## Regla no negociable de esta fase

Cero falsos positivos y cero tests verdes vacíos. Un test solo cuenta si
protege una regresión medida contra datos reales. Si una capacidad no se
puede hacer confiable con las herramientas actuales (caso probable: triaje
automático de redes sociales), la respuesta correcta es **degradar la
presentación a "revisión humana" y decirlo**, NO inventar un clasificador que
apruebe un test pero mienta al cliente.

## Los 3 workstreams

### WS1 — Seed de `watched_brands` para demo
Poblar una cuenta demo con marcas/dominios públicos, correr el ciclo real, y
dejar hallazgos reales visibles en `GET /antisuplantacion/dominios`. Datos de
demo claramente etiquetados como tales (no se representa a esas marcas).

### WS2 — Eval de regresión de antisuplantación
Harness análogo a `eval/run_eval.py` pero para lookalikes: sobre un snapshot
real congelado, verificar que el pipeline (generación de variantes →
detección de registro → triaje de defensivos → urgentes) produce la
clasificación esperada. **Anti-circular**: las etiquetas del ground truth no
pueden derivarse de la misma lógica que se está midiendo; documentar qué
señal independiente respalda cada etiqueta. Mide precisión sobre el conjunto
"urgente" y detecta si una regresión rompe la separación defensivo/tercero.

### WS3 — Triaje honesto de redes sociales
Medir sobre la salida real de Sherlock si es posible separar con confianza
el perfil oficial del cliente (señal candidata: el perfil enlaza al dominio
legítimo) de los demás. Si es confiable, cablearlo igual que el de dominios
(marcar en el nodo, filtrar/degradar en el builder). Si NO es confiable con
las señales disponibles, degradar todos los perfiles a severidad `info` como
"lista para revisión humana" y documentar por qué el triaje automático no
aplica. En ningún caso un perfil se reporta como amenaza sin evidencia.

## Criterio de aceptación

- WS1: `GET /antisuplantacion/dominios` de la cuenta demo devuelve hallazgos
  reales, con los defensivos ya excluidos y los urgentes como critical.
- WS2: `py -3 eval/run_eval_antisuplantacion.py` corre, reporta métricas sobre
  el snapshot real, y falla si se rompe la separación defensivo/tercero.
- WS3: ningún perfil de red social se persiste como amenaza (high/critical)
  sin una señal que lo respalde; la decisión (triaje confiable vs. revisión
  humana) queda documentada con la medición que la justifica.
- Suite completa verde, y cada test nuevo protege una regresión real medida.

## Fuera de alcance

- No tocar `vigia_core_private/`. No features de producto nuevas (UI, billing).
- No pushear a `origin` (público). Commit + `git push privado main:producto`.
- La Ley 2573 como argumento de venta se resuelve aparte (con un abogado).
