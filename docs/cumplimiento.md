# Módulo de Cumplimiento Normativo (Ley 2573 de 2026 / ISO 27001) — Item 6

Sigue el estilo de `eval/live_run_report.md`: esto documenta una corrida real
contra datos reales, no un diseño teórico sin probar.

## Qué se construyó

- **`agents/cumplimiento.py`** — módulo nuevo, NO es un nodo del grafo
  LangGraph (a diferencia de `agents/reporteria.py`). Se invoca bajo demanda
  sobre el historial completo de `findings` de un tenant (potencialmente
  varios scans en el tiempo), porque el gancho de venta
  (`docs/market-research.md` sección 3) es evidencia acumulada de
  trazabilidad, no una foto de un solo escaneo.
- **`GET /reports/cumplimiento`** en `api/main.py` — endpoint nuevo, mismo
  patrón de auth que el resto (`Depends(get_current_user)`), acepta
  `contexto_negocio` como query param opcional (mismo campo que ya usa
  `agents/priorizacion.py`).
- Fix real de un bug preexistente en **`agents/_llm.py`** (ver abajo).

## El mapeo — qué tan específico es, y por qué no más

`docs/market-research.md` sección 3 es la única fuente de detalle legal del
proyecto. Da **cuatro obligaciones concretas en prosa**, sin numeración de
artículos de la Ley 2573:

1. Adoptar medidas de seguridad digital "suficientes y razonables" para
   validar identidad de usuarios.
2. Atender oportunamente reportes de presunta suplantación.
3. Conservar evidencia de los procedimientos de validación aplicados
   (trazabilidad).
4. Poder demostrar, si se les exige, que sus mecanismos de prevención de
   fraude eran adecuados.

**No hay numeración de artículos en ninguna fuente del repo** (ni
`market-research.md` ni `plan-proyecto-ciberseguridad.md`). Por diseño,
`agents/cumplimiento.py` tampoco la inventa — el mapeo a Ley 2573 vive a
nivel de estas 4 obligaciones documentadas, no a nivel de artículo. Si en
algún momento se consigue el texto legal completo con numeración real, ahí
es donde afinar esto — no antes.

El documento también trae una advertencia explícita de alcance (mismo
archivo, sección 3): la ley apunta primero a entidades financieras,
telecomunicaciones y comercio con crédito, no a "toda pyme" automáticamente.
Esa advertencia (`LEY2573_ADVERTENCIA_ALCANCE` en el código) se incluye
**siempre**, en todo reporte generado — nunca se vende como aplicabilidad
legal garantizada.

Los identificadores de control **ISO 27001 sí son específicos** (`A.8.9`,
`A.5.15`, `A.8.26`, etc.) porque son del Anexo A publicado de
ISO/IEC 27001:2022 — un estándar público, no algo que `market-research.md`
necesitara detallar (ese documento solo menciona "ISO 27001" por nombre).
Aun así, decidir qué control corresponde a qué categoría de hallazgo es una
interpretación razonada de este proyecto, no una certificación —
`ISO27001_ADVERTENCIA` también viaja en cada reporte y recomienda validación
por un auditor certificado antes de usarse como evidencia formal.

## La taxonomía de categorización (por qué existe una capa intermedia)

Hallazgo real de esta sesión, no supuesto: `findings.tipo` en la base de
datos vale `"desconocido"` para prácticamente todo hallazgo que viene de
ZAP/Nuclei. Causa raíz en `api/main.py::scan()` (línea ~501):

```python
hallazgo.get("tipo") or hallazgo.get("type") or "desconocido"
```

pero el hallazgo verificado (`verified_findings`, ver `agents/verificacion.py`)
nunca tiene esas claves en el nivel superior — solo dentro de `raw`. Mapear
directo por `tipo` habría dejado casi todo en un cajón genérico sin valor de
venta, así que la función de categorización mira dentro de `raw_json`
(nombre de alerta de ZAP, `cweid`, tags/severity de Nuclei, regla de
Semgrep) para inferir una categoría útil, cada una con su propio mapeo a
ISO 27001 y Ley 2573.

**Nota (migración open-core):** la taxonomía real (13 categorías afinadas
contra datos reales, con su mapeo específico a controles ISO/IEC 27001
Anexo A y obligaciones de la Ley 2573) es la pieza de IP más diferenciada
del proyecto (ver `docs/market-research.md` original, ahora solo en el
paquete privado) y vive en `vigia_core_private/vigia_core_private/cumplimiento.py`
(fuera de este repo público, ver `docs/open-core.md`). `agents/cumplimiento.py`
en este repo cae a una taxonomía genérica de 3 categorías si ese paquete no
está instalado. `sin_clasificar` nunca descarta un hallazgo — lo deja
documentado igual, porque la trazabilidad exige registrar todo lo
detectado.

**Bug real encontrado y corregido durante la construcción de la
categorización:** la primera versión incluía el campo `desc` (la
descripción HTML larga que ZAP escribe) en el texto usado para clasificar
por palabras clave. Contra datos reales, esto categorizó mal "Content
Security Policy (CSP) Header Not Set" como `inyeccion` (la descripción de
CSP menciona "XSS" de pasada, para explicar qué previene CSP) y
"Cross-Origin-Embedder-Policy Header Missing" como `cors_mal_configurado`
(la descripción menciona "usando CORP o CORS"). Se corrigió excluyendo
`desc`/`description` del texto de clasificación — los campos de
nombre/etiqueta/regla ya son lo bastante descriptivos sin ese ruido. Ver
`_texto_buscable()` en `agents/cumplimiento.py` para el detalle y el
docstring que documenta este caso exacto.

## Limitación conocida — CERRADA (ver `eval/live_run_report.md`, Corrida 10)

Esta sección documentaba, a propósito, una brecha no resuelta en este item:
`agents/antisuplantacion.py` genera `antisuplantacion_findings` dentro del
`PipelineState` de un scan, pero `api/main.py::scan()` solo insertaba
`verified_findings` (rama técnica) en la tabla `findings` — las señales de
dnstwist/Sherlock de esa función nunca llegaban a la base de datos de forma
consultable después del request HTTP. La excepción real era (y sigue siendo,
para vigilancia continua) `api/certstream_listener.py::registrar_finding_certstream()`.

**Cerrada en una sesión posterior** (Corrida 10 de `eval/live_run_report.md`):
`api/main.py::_extraer_findings_antisuplantacion()` ahora aplana
`antisuplantacion_findings` a filas `findings` reales, colgadas del mismo
`scan_id` real que `POST /scan` ya crea (sin necesitar una fila `scans`
sintética, a diferencia de CertStream). Usa los mismos `tipo` que
`_categorizar_hallazgo()` de este módulo ya reconocía (`"dominio_variante"`,
`"perfil_red_social"`) desde que se escribió — la brecha era 100% de
persistencia, no de categorización, tal como se anotó abajo en su momento.
Verificado en vivo contra un dominio sintético propio (`miempresatest.com`,
sin tocar ningún tercero real): 6 hallazgos reales (2 dominio-variante, 4
perfiles de red social) insertados, surfaceados correctamente por
`GET /findings` y por este mismo endpoint (`GET /reports/cumplimiento`,
`total_hallazgos: 6`, categorizados sin tocar este archivo). No se modificó
ninguna línea de `agents/cumplimiento.py` para cerrar esta brecha — exactamente
como se predijo abajo.

## Bug preexistente encontrado y corregido: mojibake en el fallback de CLI

Al generar el primer reporte real con el fallback `claude -p` (sin
`ANTHROPIC_API_KEY` configurada, igual que documenta Item 1 en
`HANDOFF.md`), el texto en español salía corrupto: `"Gestión"` aparecía como
`"GestiÃ³n"`, etc. Causa raíz confirmada con una prueba aislada
(`subprocess.run` directo contra el binario `claude`, comparando bytes
crudos vs. texto decodificado): `agents/_llm.py::_call_via_cli` llamaba
`subprocess.run(cmd, capture_output=True, text=True, timeout=...)` sin
`encoding` explícito. En Windows, `text=True` sin `encoding` decodifica con
`locale.getpreferredencoding(False)`, que en esta máquina es `cp1252` — pero
la CLI de Claude siempre escribe stdout en UTF-8. Cada tilde/ñ se
corrompía de forma **no recuperable** (bytes UTF-8 mal-decodificados como
cp1252 y luego vueltos a codificar).

Esto no es un bug de este item — es un bug preexistente en
`agents/_llm.py` que afecta a **todos** los agentes que usan el fallback de
CLI (`reporteria.py`, `remediacion.py`, `priorizacion.py`, y ahora
`cumplimiento.py`) cada vez que generan español con tildes sin una API key
real configurada. Se corrigió con un cambio de una línea (agregar
`encoding="utf-8"` al `subprocess.run`), verificado con una prueba aislada
antes y después del fix, y luego confirmado end-to-end en el reporte de
cumplimiento real (ver evidencia abajo). Vale la pena correr los otros
agentes (reportería, remediación, priorización) contra datos reales para
confirmar que también quedaron arreglados — no se hizo en esta sesión por
estar fuera del alcance directo del Item 6, pero el fix es compartido.

## Verificación real — no simulada

1. **Generación de datos reales**: no había hallazgos en `ciberseguridad.db`
   al empezar (`SELECT COUNT(*) FROM findings` = 0, a pesar de que
   `HANDOFF.md` mencionaba historial de demo — la cuenta `demo@vigia.local`
   existía pero sin scans). Se levantó OWASP Juice Shop real vía Docker
   (`docker run -d -p 3050:3000 bkimminich/juice-shop`, imagen oficial) y se
   corrió `POST /scan` contra `http://localhost:3050` con
   `autorizacion_firmada: true` (lab app, cumple la regla de oro de
   `HANDOFF.md`/sección 0 del plan maestro). Resultado real: **10 hallazgos
   de ZAP baseline** insertados en `findings` para el tenant
   `vigia-demo` (CSP Header Not Set, Cross-Domain Misconfiguration,
   Cross-Origin-Embedder-Policy/Opener-Policy Header Missing, Dangerous JS
   Functions, Deprecated Feature Policy Header Set, Timestamp Disclosure,
   Modern Web Application, Storable/Non-Cacheable Content).
2. **Categorización verificada contra cada hallazgo real** (no solo el
   agregado): se imprimió `_categorizar_hallazgo()` para los 10 hallazgos +
   2 hallazgos de `dominio_variante_certstream` de otro tenant de prueba
   (`certstream-test-co`, generado por el trabajo paralelo de Item 5 en
   esta misma sesión) — las 12 categorías asignadas se revisaron a mano y
   son correctas (ver bug de `desc` arriba, encontrado y arreglado
   justamente por esta verificación manual).
3. **Reporte end-to-end vía HTTP real**: `GET /reports/cumplimiento` contra
   un servidor uvicorn real (puerto 48179, separado del servidor de otro
   agente activo en la misma sesión para no interferir), con JWT real del
   login de `demo@vigia.local`, devolvió `200 OK` con el reporte completo
   sobre los 10 hallazgos reales de ese tenant (aislamiento multi-tenant
   confirmado: los 2 hallazgos del tenant `certstream-test-co` NO
   aparecieron en la respuesta de `vigia-demo`, como debe ser).
4. **Contenido del reporte revisado a mano**: usa razonamiento real de
   Claude (fallback CLI, sin API key configurada — mismo mecanismo de Item
   1), no una plantilla estática. Menciona correctamente el contexto de
   negocio (`contexto_negocio` de query param), explica la inversión de
   carga de la prueba de Ley 2573, incluye la advertencia de alcance legal
   completa, la advertencia de ISO 27001, prioriza acciones de forma
   razonable (dominio-variante primero por severidad alta), y nunca
   inventa numeración de artículos ni afirma aplicabilidad legal
   garantizada.
5. **Codificación UTF-8 confirmada con los bytes crudos**, no solo con la
   vista de un editor (que puede enmascarar el problema): se inspeccionó el
   payload JSON crudo del endpoint con un hexdump — el em-dash `—`
   aparece como `\xe2\x80\x94` (UTF-8 correcto), y comillas tipográficas
   `""` se preservan igual.

## Limpieza post-verificación

Se detuvo y eliminó el contenedor `juice-shop-vigia` y el servidor uvicorn
de prueba (puerto 48179) al terminar. El servidor de otro agente activo en
esta sesión (puerto 48173) se dejó corriendo sin tocar. La base de datos
`ciberseguridad.db` conserva los 3 scans y 10 findings reales generados
contra Juice Shop — quedan disponibles para seguir probando este módulo o
`eval/run_eval.py` en sesiones futuras.

## Qué falta (fuera de alcance de este item, anotado para el backlog)

- ~~Persistir `antisuplantacion_findings` (dnstwist/Sherlock bajo demanda) en
  la tabla `findings`~~ — **cerrado**, ver "Limitación conocida" arriba y
  `eval/live_run_report.md` Corrida 10.
- Endpoint de descarga en PDF/DOCX del reporte de cumplimiento — mencionado
  como pendiente transversal en `HANDOFF.md` ("reportes descargables"), el
  skill `anthropic-skills:pdf`/`docx` ya está disponible en este entorno
  para cuando se aborde.
- Confirmar que el fix de codificación UTF-8 en `agents/_llm.py` no rompió
  nada en `reporteria.py`/`remediacion.py`/`priorizacion.py` corriéndolos
  de nuevo contra un scan real (no se hizo en esta sesión).
- Revisión por un abogado colombiano y un auditor ISO 27001 certificado del
  mapeo concreto antes de usarlo en una venta real — el código y este
  documento lo dejan explícito, pero vale repetirlo aquí.
