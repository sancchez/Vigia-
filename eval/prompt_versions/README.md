# Versionado de prompts

Aplica el punto 3 de la seccion 8.2 del plan maestro: *"cada prompt de la
seccion 7 vive en git, versionado igual que el codigo. Un cambio de prompt es
un commit, no una edicion perdida en un chat."*

## Convencion de nombres

```
eval/prompt_versions/v{N}_{nombre_agente}.md
```

- `{N}`: numero de version entera, empieza en `1`. Nunca se reescribe un
  archivo de version anterior — se crea uno nuevo (`v2_...`, `v3_...`) y el
  anterior queda intacto como historial.
- `{nombre_agente}`: slug en snake_case del agente, uno de los 8 definidos en
  la seccion 7 del plan maestro:
  - `orquestador`
  - `recon`
  - `escaneo`
  - `verificacion`
  - `priorizacion`
  - `remediacion`
  - `reporteria`
  - `anti_suplantacion`

Archivos actuales (todos v1, extraidos literalmente del plan maestro el
2026-07-14):

- `v1_orquestador.md`
- `v1_recon.md`
- `v1_escaneo.md`
- `v1_verificacion.md`
- `v1_priorizacion.md`
- `v1_remediacion.md`
- `v1_reporteria.md`
- `v1_anti_suplantacion.md`

## Estructura de cada archivo de version

Cada archivo de prompt debe contener, en este orden:

1. **Encabezado** con: nombre del agente, numero de version, fecha de
   creacion de esa version.
2. **Prompt** — el texto exacto del system prompt, dentro de un bloque de
   codigo, sin parafrasear ni "mejorar" silenciosamente.
3. **Changelog** — lista de cambios de esa version respecto a la anterior
   (en `v1` siempre dice "version inicial extraida del plan").

## Cuando bumpear version (crear v2, v3, ...)

Bumpea version cuando:

- Cambias una sola palabra o instruccion del texto del prompt que pueda
  alterar el comportamiento del agente (aunque parezca cosmetico).
- Agregas o quitas una restriccion, precondicion, o ejemplo few-shot.
- Cambias el modelo o los parametros de invocacion asociados a ese prompt de
  forma que afecte el comportamiento esperado (documentalo en el changelog
  aunque el texto del prompt no cambie).

NO bumpees version por:
- Arreglar una errata sin cambiar el significado (documentalo igual en el
  changelog de la version vigente como "fix menor", pero no es obligatorio
  crear un archivo nuevo solo por eso — usa criterio; si hay duda, bumpea).

## Relacion con el loop de evaluacion (seccion 8.2)

Segun el punto 4 de la seccion 8.2: **ningun cambio de prompt pasa a
produccion sin antes correr el set de evaluacion completo**
(`eval/run_eval.py` contra `eval/ground_truth.yaml`). El flujo esperado para
publicar una nueva version de un prompt es:

1. Crear `v{N+1}_{agente}.md` con el prompt nuevo y su changelog.
2. Correr `python eval/run_eval.py` contra el set de evaluacion (Juice Shop,
   ver `eval/setup_juiceshop.md`) usando el pipeline con el prompt nuevo.
3. Comparar precision/recall contra la version anterior (guardados como
   referencia en `eval/failure_log.md` o en el historial de commits).
4. Si el recall baja o suben los falsos positivos, **no se despliega** esa
   version, aunque un caso suelto se vea mejor — el archivo `v{N+1}` puede
   quedar en git como experimento documentado, pero el pipeline en produccion
   sigue apuntando a la version anterior hasta que una nueva version SI mejore
   las metricas.
5. Si mejora o al menos no empeora las metricas, se despliega y se anota en
   `eval/failure_log.md` (si aplico a un fallo especifico) o en el commit
   message.

## Registro rapido de versiones

| Agente | Version vigente | Fecha | Archivo |
|---|---|---|---|
| Orquestador | v1 | 2026-07-14 | `v1_orquestador.md` |
| Recon (pasivo) | v1 | 2026-07-14 | `v1_recon.md` |
| Escaneo (activo) | v1 | 2026-07-14 | `v1_escaneo.md` |
| Verificacion | v1 | 2026-07-14 | `v1_verificacion.md` |
| Priorizacion | v1 | 2026-07-14 | `v1_priorizacion.md` |
| Remediacion | v1 | 2026-07-14 | `v1_remediacion.md` |
| Reporteria | v1 | 2026-07-14 | `v1_reporteria.md` |
| Anti-Suplantacion | v1 | 2026-07-14 | `v1_anti_suplantacion.md` |
