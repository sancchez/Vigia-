# Open-core: qué es público, qué es privado, y cómo se conecta

Este documento explica la división open-core de Vigia: el repo público
(`ciberseguridad`, este repo) se mantiene 100% cloneable, instalable y
demostrable por sí solo -- es la prueba de calidad de ingeniería que
sostiene la estrategia de transparencia del proyecto (ver
`docs/produccion-readiness.md` y el `README.md`). Pero la IP realmente
diferenciada del producto vive en un paquete privado separado,
`vigia_core_private/`, que **nunca se publica** y que este repo intenta
importar en tiempo de ejecución con un fallback genérico si no está
presente.

Analogía: el mismo patrón que usan GitLab CE/EE o proyectos similares --
el motor es real y auditable, la capa comercial diferenciada es aparte.

## Qué se movió a `vigia_core_private/` y por qué

| Contenido | Archivo público que lo usa | Por qué es IP real (no solo "wrapping") |
|---|---|---|
| System prompt de priorización por impacto de negocio | `agents/priorizacion.py` | Encodea la heurística real de "qué pesa más para ESTE cliente" -- no es CVSS genérico, es prompt-engineering afinado sobre contexto de negocio. |
| System prompt de remediación | `agents/remediacion.py` | El estilo/estructura de redacción para lector no técnico, con ejemplo concreto -- trabajo de prompt-engineering, no boilerplate. |
| System prompt de reportería | `agents/reporteria.py` | La plantilla editorial real del reporte ejecutivo de Vigia. |
| System prompt + 5 categorías finas de revisión de código con IA | `agents/revision_ia.py` | El desglose (`control_acceso`, `aislamiento_multitenant`, `confianza_input_cliente`, `logica_de_negocio`, `gestion_sesion_o_token`) es lo que distingue este agente de un "revisa este código" genérico -- ver el docstring del módulo, "decisión de diseño 1". |
| Taxonomía de 13 categorías + mapeo específico a controles ISO/IEC 27001 Anexo A + mapeo a las 4 obligaciones de la Ley 2573 de 2026 + prompt de redacción del reporte de cumplimiento | `agents/cumplimiento.py` | Según `docs/market-research.md` (ahora solo en el paquete privado), **ningún competidor en Colombia ofrece esta combinación comercialmente** -- es, con diferencia, la pieza de IP más valiosa del repo. |
| `docs/market-research.md` completo | (ninguno -- documentación, no código) | Inteligencia competitiva y estrategia de pricing. No es código de producto, pero tampoco debe viajar en un repo público que un competidor puede clonar. |

Estos 5 módulos de `agents/` viven en el paquete privado como archivos
espejo (`vigia_core_private/vigia_core_private/{priorizacion,remediacion,reporteria,revision_ia,cumplimiento}.py`).
`docs/market-research.md` vive como copia en `vigia_core_private/docs/`.

## Qué se decidió DEJAR público, y por qué

| Contenido | Por qué se queda |
|---|---|
| `tools/scan.py` (wrappers de Nuclei/ZAP/Semgrep/Trivy/Grype) | Integración directa de herramientas open source tal cual -- ningún tuning propio de umbrales o heurísticas encontrado al leer el archivo. Esto es exactamente lo que `docs/market-research.md` (sección 4) recomienda comunicar como diferencial de confianza ("así es exactamente como te escaneamos, revísalo tú mismo"), no ocultar. |
| `tools/antisuplantacion.py` (wrappers de dnstwist/Sherlock/CertStream/Safe Browsing) | Revisado línea por línea: es un wrapper delgado de subprocess/librería tal cual, sin tuning de umbrales propio más allá de exponer los parámetros nativos de cada herramienta (`registered_only`, `timeout`). No califica como IP diferenciada -- es integración, no reasoning propietario. |
| `agents/escaneo.py`, `agents/recon.py`, `agents/verificacion.py`, `agents/antisuplantacion.py`, `agents/orquestador.py`, `orchestrator/*` | Lógica de orquestación/grafo y verificación determinista (sin LLM) -- arquitectura, no el "cómo pensamos" comercial. Es exactamente la parte que la estrategia de transparencia quiere mostrar. |
| `api/*`, `db/*` | Infraestructura de producto (auth, multi-tenancy, endpoints, schema) -- necesaria para que el repo sea runnable y auditable, no es la capa de razonamiento diferenciado. |
| La ESTRUCTURA de `agents/cumplimiento.py` (por qué es función bajo demanda y no nodo del grafo, el bug de `tipo="desconocido"`, la nota de persistencia de anti-suplantación) | Es narrativa de arquitectura/decisiones de ingeniería, no el contenido propietario en sí. Se mantiene en el docstring público, pero se removió la enumeración explícita de las 13 categorías + sus controles ISO específicos (antes revelada en el docstring y en `docs/cumplimiento.md`; ver "Redacciones" abajo). |
| `docs/cumplimiento.md` (el resto del documento) | Se conserva como historial de verificación real (Corridas, bugs encontrados/corregidos), pero se redactó el párrafo que enumeraba las 13 categorías -- ver "Redacciones" abajo. |

## Redacciones adicionales (más allá de mover código)

Mover el *código* de la taxonomía a privado no sirve de nada si la
*documentación* pública sigue enumerando las mismas 13 categorías en
prosa. Se redactaron dos lugares que antes lo hacían:

1. El docstring de módulo de `agents/cumplimiento.py` -- reescrito para
   explicar el diseño (por qué existe la capa de categorización, la
   brecha de persistencia ya cerrada) sin enumerar las 13 categorías ni
   sus controles ISO específicos.
2. `docs/cumplimiento.md`, sección "La taxonomía de categorización" --
   se quitó la lista completa de 13 nombres de categoría, reemplazada por
   una referencia a `vigia_core_private/`.

## Cómo funciona el fallback (mecanismo)

Cada módulo público que consume IP privada sigue el mismo patrón:

```python
try:
    from vigia_core_private.<modulo> import SYSTEM_PROMPT  # y/o datos
    _MODO = "privado"
except ImportError:
    SYSTEM_PROMPT = "<versión genérica, claramente más simple>"
    _MODO = "fallback_publico"
```

- **`agents/priorizacion.py`, `agents/remediacion.py`, `agents/reporteria.py`**:
  solo el `SYSTEM_PROMPT` cambia. El fallback público sigue siendo un
  prompt real y útil (reordena por CVSS, redacta remediación genérica por
  tipo de vulnerabilidad, compila un reporte básico) -- solo menos
  sofisticado que la versión privada.
- **`agents/revision_ia.py`**: además del prompt, la lista de categorías
  (`CATEGORIAS`) y el `INSTRUCCION_FORMATO` (el esquema JSON de salida)
  vienen del paquete privado. El fallback usa una sola categoría amplia
  `logica_de_negocio` en vez del desglose fino de 5.
- **`agents/cumplimiento.py`** (el caso más profundo): además del
  `SYSTEM_PROMPT`, importa `CATEGORIA_MAPEO`, `LEY2573_OBLIGACIONES`,
  `LEY2573_ADVERTENCIA_ALCANCE`, `ISO27001_ADVERTENCIA`, y la función
  `categorizar_hallazgo` completa (con sus reglas de palabras clave
  reales). El fallback define su propia versión de cada uno,
  deliberadamente genérica: 3 categorías amplias (`hallazgo_tecnico`,
  `suplantacion_marca`, `sin_clasificar`) sin mapeo específico a
  controles. El resultado del reporte incluye un campo `"modo"`
  (`"privado"` o `"fallback_publico"`) para que cualquier consumidor
  (frontend, API, o quien lea el JSON) sepa qué tan detallado es el
  reporte que recibió.

En todos los casos: **el repo público nunca falla ni queda en un estado
inconsistente si `vigia_core_private` no está instalado** -- es el
comportamiento esperado y probado (ver "Verificación" abajo), no un caso
de error.

## El paquete privado en sí

- Ubicación: `vigia_core_private/` en la raíz del repo (mismo nivel que
  `agents/`, `tools/`, etc.).
- **Está en `.gitignore` de este repo público** -- nunca puede
  commitearse aquí por accidente.
- **Es su PROPIO repositorio git**, inicializado con `git init` dentro de
  `vigia_core_private/`, completamente separado del historial de este
  repo público. Tiene su propio primer commit con todo el contenido
  descrito arriba.
- **No se creó ningún remoto** (ni en GitHub ni en ningún otro host) --
  eso es una acción de cuenta real que el usuario debe hacer manualmente
  si quiere respaldo fuera de esta máquina. Pasos sugeridos (manuales,
  fuera del alcance de este agente):
  1. Crear un repositorio **privado** nuevo en GitHub (o el host que
     prefieras) -- por ejemplo `vigia-core-private`.
  2. `cd vigia_core_private && git remote add origin <url-del-repo-privado>`
  3. `git push -u origin main`
- Estructura:
  ```
  vigia_core_private/
    pyproject.toml
    vigia_core_private/
      __init__.py
      priorizacion.py     # SYSTEM_PROMPT real
      remediacion.py      # SYSTEM_PROMPT real
      reporteria.py       # SYSTEM_PROMPT real
      revision_ia.py      # SYSTEM_PROMPT + CATEGORIAS + INSTRUCCION_FORMATO reales
      cumplimiento.py      # taxonomía de 13 categorías + mapeos + categorizar_hallazgo() + SYSTEM_PROMPT
    tests/
      test_cumplimiento_categorizacion.py  # movido desde el repo público (ver abajo)
    eval/
      cumplimiento_fixture_juiceshop.json  # copia del fixture real (también existe en el repo público, para scripts/seed_demo.py)
    docs/
      market-research.md   # movido desde el repo público
  ```
- Para desarrollar con la capa real activa: `pip install -e vigia_core_private/`
  desde la raíz del repo público (entorno virtual compartido). Sin ese
  `pip install`, el repo público corre igual, en modo fallback.

## Tests: qué se movió, qué se quedó, y por qué

- **Movido a `vigia_core_private/tests/test_cumplimiento_categorizacion.py`**:
  el test que existía en `tests/test_cumplimiento_categorizacion.py` del
  repo público asertaba nombres de categoría EXACTOS de la taxonomía real
  (`cabeceras_seguridad_faltantes`, `cors_mal_configurado`, etc.) contra un
  fixture de datos reales de ZAP/Juice Shop. Correr ese test en el repo
  público (sin el paquete privado instalado) fallaría siempre por diseño
  -- no es una regresión a arreglar, es la taxonomía que ya no vive ahí.
  Se movió tal cual (con el import actualizado a
  `vigia_core_private.cumplimiento`) para que la regresión que protege
  (el bug histórico de "CSP Header Not Set categorizado como inyección")
  se siga vigilando donde vive el código que protege.
- **Nuevo en el repo público, `tests/test_cumplimiento_fallback.py`**:
  reemplaza al anterior en el repo público. Verifica el contrato, no la
  taxonomía exacta: que el módulo siempre sabe en qué modo está
  (`"privado"` o `"fallback_publico"`), que `categorizar_hallazgo` nunca
  devuelve una categoría que no existe en el `CATEGORIA_MAPEO` activo
  (evita un `KeyError` real en producción), que el reporte se genera de
  punta a punta sobre los 10 hallazgos reales de Juice Shop sin
  excepciones, y que el modo fallback expone exactamente las 3 categorías
  genéricas esperadas (nunca la taxonomía real, si el paquete no está
  instalado).
- **`eval/cumplimiento_fixture_juiceshop.json` se queda en el repo
  público** (además de tener una copia en el paquete privado): lo sigue
  usando `scripts/seed_demo.py` para poblar datos de demo reales sin
  depender de Docker/ZAP en el momento de la demo. El campo
  `expected_categoria` de cada fila del fixture corresponde a la
  taxonomía real y no se usa en el repo público (solo lo lee el test que
  ahora vive en el paquete privado) -- se dejó el fixture completo tal
  cual, sin quitar ese campo, porque removerlo hubiera requerido tocar
  `scripts/seed_demo.py` sin necesidad real (ese script solo lee
  `raw_json`/`endpoint`/`severidad`, nunca `expected_categoria`).
- Los demás módulos movidos (`priorizacion`, `remediacion`, `reporteria`,
  `revision_ia`) no tenían tests que asertaran el contenido exacto del
  prompt -- no hizo falta mover ni duplicar nada ahí.

## Verificación realizada

1. **Modo fallback (paquete privado NO instalado, el estado normal de
   este checkout)**: `pytest -q` corrido sobre el repo tal cual queda
   después de esta migración. Confirmado: la suite completa pasa,
   incluyendo el nuevo `test_cumplimiento_fallback.py`, sin que
   `vigia_core_private` esté en el `PYTHONPATH`.
2. **Modo privado (paquete instalado)**: `pip install -e vigia_core_private/`
   en el mismo entorno, re-corrido de `pytest -q` en el repo público --
   confirmado que `agents.cumplimiento._MODO_CUMPLIMIENTO == "privado"` y
   que el resto de la suite pública sigue en verde exactamente igual (el
   comportamiento público no depende de qué modo esté activo, salvo el
   propio contenido del reporte). Además, `pytest -q` corrido DENTRO de
   `vigia_core_private/` (con ese mismo install) para confirmar que el
   test movido (`test_cumplimiento_categorizacion.py`) sigue pasando ahí,
   con las mismas 13 aserciones exactas de siempre.
3. Se importaron a mano los 5 módulos públicos afectados
   (`agents.priorizacion`, `.remediacion`, `.reporteria`, `.revision_ia`,
   `.cumplimiento`) en ambos escenarios (con y sin el paquete privado) para
   confirmar que ningún `ImportError` no manejado se escapa del bloque
   `try/except`.

## Contenido ya committeado localmente (no pusheado) -- pendiente de decisión

Los 6 commits locales de esta sesión (`d8e58f3` .. `029348c`, ninguno
pusheado a `origin/main`, confirmado con `git fetch origin && git log
origin/main..HEAD`) ya contienen, en sus diffs, el contenido real que esta
migración movió a privado (los system prompts exactos, la taxonomía
completa de `agents/cumplimiento.py`, y `docs/market-research.md`
completo). Esta migración solo cambió el ÁRBOL DE TRABAJO actual
(`agents/*.py`, `.gitignore`, `docs/*`, `tests/*`) -- deliberadamente NO
se reescribió ningún commit existente (`git rebase`, `git filter-branch`,
etc.), porque eso es más invasivo y el usuario pidió explícitamente que
se consultara antes de tocar historial ya commiteado. Ver el mensaje a
`main` para el detalle de las opciones y la pregunta abierta.
