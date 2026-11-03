"""Normalización determinista de TIPO y UBICACIÓN para que un hallazgo
correcto pueda matchear el ground truth (Fase 9, Grupo 0 -- ver
`openspec/changes/fase9-motor-dirigido/proposal.md`, sección 6).

Problema real que resuelve
--------------------------
`eval/run_eval.py` compara `finding.type == gt.type` (string exacto) y
`finding.location` contra `gt.location` (prefijo/segmentos de path). Pero:

1. ZAP reporta el nombre de la alerta tal cual la conoce ("SQL Injection -
   MySQL"), no el tipo canónico del ground truth (`sql_injection`). Sin un
   mapa, NINGÚN hallazgo de ZAP puede cruzar el eval aunque sea correcto.
2. La ubicación que trae el hallazgo (`raw_json.url` o el campo exportado de
   la fila persistida) suele ser una URL absoluta
   (`http://host.docker.internal:8078/vulnerabilities/sqli/?id=...`), mientras
   el ground truth usa un path relativo (`/vulnerabilities/sqli/`). Sin
   recortar esquema/host/query, comparten 0 segmentos desde el inicio.

Evidencia medida (no se re-deriva, ver `eval/failure_log.md`, fila DVWA
re-medición 2026-07-25): normalizando ambos ejes sobre
`eval/baseline_real/dvwa_autenticado_2026-07-25_findings.json` (1047 alertas
crudas), el recall real pasa de 0.0 (artefacto de medición) a 3/11 = 0.273.

Mismo patrón que `tools/clasificacion.py`: función pura, determinista, sin
LLM, auditable (`git blame` explica cada entrada por qué existe).

Criterio de inclusión en `MAPA_TIPOS_ZAP` (deliberadamente estricto)
---------------------------------------------------------------------
- Match EXACTO (no prefijo/substring difuso) contra el nombre de alerta ya
  normalizado (`strip().lower()`) -- un mapeo laxo generaría falsos
  positivos, que es exactamente lo que este módulo existe para evitar.
- Solo nombres de alerta VERIFICADOS contra una corrida real en
  `eval/baseline_real/` (no se inventa vocabulario de ZAP).
- Los hallazgos "Potential" (sospecha, no confirmación -- ej. "User
  Controllable HTML Element Attribute (Potential XSS)") NUNCA entran acá.
  Contarlos como acierto sería inflar el recall con ruido: Vigia no puede
  confirmar esos 3 XSS de DVWA (xss_r/xss_s/xss_d) con la evidencia actual,
  y decir lo contrario sería mentirle al cliente.
"""

from __future__ import annotations

from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Mapa nombre-de-alerta-ZAP (exacto, en minúsculas) -> tipo canónico del GT
# ---------------------------------------------------------------------------
# Cada entrada documenta el nombre real de alerta que la origina y la corrida
# donde se verificó, para que sea auditable sin tener que re-correr un scan.
MAPA_TIPOS_ZAP: dict[str, str] = {
    # Verificado: DVWA autenticado 2026-07-25 (2 instancias) contra
    # /vulnerabilities/exec/ y /vulnerabilities/javascript/ -- confirma
    # DVWA-003 (solo la instancia en /exec/ matchea esa ubicación; la de
    # /javascript/ queda correctamente sin matchear ningún VULN del GT).
    "remote os command injection (time based)": "command_injection",
    # Verificado: DVWA autenticado 2026-07-25 (1 instancia), CWE-22, contra
    # /vulnerabilities/fi/?page=%2Fetc%2Fpasswd, evidencia "root:x:0:0" --
    # confirma DVWA-005. NOTA DE AMBIGÜEDAD (documentada, no resuelta): el
    # ground truth de DVWA llama a esta clase "file_inclusion" (así se llama
    # el módulo de DVWA), pero Juice Shop (VULN-008) y WebGoat (WEBGOAT-010)
    # llaman a la misma familia CWE-22 "path_traversal". Este mapa es una
    # función pura nombre->tipo, así que no puede dar dos respuestas distintas
    # para el mismo nombre de alerta según el target. Se elige "file_inclusion"
    # porque es la única evidencia real verificada hoy (DVWA); no se ha visto
    # todavía un caso real donde ZAP emita "Path Traversal" contra Juice Shop
    # o WebGoat -- si eso ocurre, esta entrada necesitará un desambiguador
    # adicional (ej. por endpoint/CWE combinado), no un cambio a ciegas.
    "path traversal": "file_inclusion",
    # Verificado: ZAP Active Scan, alerta confirmada (no "Potential") -- las
    # 3 variantes fijas que emite la regla de XSS reflejado/persistente/DOM de
    # ZAP (nombres estables de la herramienta, no dependen del backend como
    # las de SQLi). "Cross Site Scripting (Reflected)" apareció en la tabla
    # `findings` de producción contra testasp.vulnweb.com.
    "cross site scripting (reflected)": "xss_reflected",
    "cross site scripting (persistent)": "xss_stored",
    "cross site scripting (dom based)": "xss_dom",
}

# Auditoría 2026-07-25 (pedida por el coordinador tras revisar este módulo):
# barrido de TODOS los nombres de alerta explotables en `eval/baseline_real/*`
# + tabla `findings` de `ciberseguridad.db` contra `tipo_canonico()`. 3 nombres
# quedaron deliberadamente SIN mapear, con la justificación explícita acá para
# que quede auditable (no es un olvido):
#
# - "Absence of Anti-CSRF Tokens" -> NO se mapea a `csrf`. Es una observación
#   PASIVA de marcado HTML (falta el campo de token en el form), no una
#   confirmación activa de que la petición falsificada realmente funciona.
#   Un formulario puede estar protegido igual por SameSite=Lax/Strict o por
#   exigir un header custom -- cosas que esta regla de ZAP no verifica. Con el
#   criterio explícito del usuario ("cero hallazgos que no signifiquen nada")
#   y "ante la duda, no mapear", se excluye: contarla mapearía DVWA-004 como
#   acierto sin haber demostrado el CSRF real, mismo error de fondo que ya se
#   evitó con los "Potential XSS".
# - "Cross-Domain JavaScript Source File Inclusion" -> NO se mapea. No
#   corresponde a ningún `type` del vocabulario de `ground_truth.yaml`,
#   `ground_truth_dvwa.yaml` ni `ground_truth_webgoat.yaml` -- es una alerta
#   de higiene/configuración (script de terceros sin integridad de subrecurso
#   verificada), no una clase de vulnerabilidad explotable del catálogo.
# - "External Redirect" -> NO se mapea. Tampoco existe un tipo
#   `open_redirect` (ni equivalente) en ninguno de los tres ground truth
#   vigentes; mapearlo sería inventar una categoría que el eval no puede
#   validar contra nada.
#
# Si una fase futura agrega `csrf`/`open_redirect` con evidencia de
# explotación confirmada (no solo ausencia de mitigación) a algún GT, estas
# tres entradas se reevalúan ahí, con el mismo criterio.

# Familia "SQL Injection" de ZAP: el mismo plugin de Active Scan emite un
# sufijo distinto por motor de BD detectado -- "SQL Injection" (genérico, sin
# motor identificado; verificado: Juice Shop autenticado 2026-07-25, único
# hallazgo explotable real de esa corrida, /rest/products/search?q=%27%28),
# "SQL Injection - MySQL" (verificado: DVWA autenticado 2026-07-25, confirma
# DVWA-001), "SQL Injection - MsSQL (Time Based)" (visto en
# `ciberseguridad.db`, confirma la misma clase contra un motor distinto).
# ZAP documenta también variantes "- PostgreSQL", "- Oracle", "- SQLite",
# "- Hypersonic SQL" para el mismo chequeo -- son la MISMA alerta explotable,
# solo cambia qué motor de BD detectó el error/retraso. Por eso se usa un
# prefijo explícito (`"sql injection"` o `"sql injection - "`) en vez de
# listar cada motor a mano: no es un match difuso (no matchea substrings en
# cualquier lado del string, solo el nombre completo empieza así), es la
# generalización auditable y documentada de una familia de nombres ya
# conocida y acotada de un único plugin de ZAP.
_PREFIJO_SQLI = "sql injection"


def tipo_canonico(nombre_alerta: str) -> str:
    """Normaliza un nombre de alerta (de ZAP u otra herramienta) al tipo
    canónico usado por los `ground_truth*.yaml` (`sql_injection`, etc.).

    Si el nombre no está en `MAPA_TIPOS_ZAP` ni en la familia SQLi, se
    devuelve tal cual (`strip().lower()`) -- mismo comportamiento que
    `run_eval.py` tenía antes de este módulo, para no cambiar nada de lo que
    ya funcionaba (hallazgos que ya nacen con el tipo canónico, ej. futuros
    chequeos dirigidos del Grupo 1+, pasan sin tocar).
    """
    clave = str(nombre_alerta or "").strip().lower()
    if clave in MAPA_TIPOS_ZAP:
        return MAPA_TIPOS_ZAP[clave]
    if clave == _PREFIJO_SQLI or clave.startswith(_PREFIJO_SQLI + " - "):
        return "sql_injection"
    return clave


def ubicacion_relativa(ubicacion: str) -> str:
    """Deriva el path relativo de una ubicación para matching contra el GT.

    - Si `ubicacion` es una URL absoluta (`http://host:puerto/path?query`),
      devuelve solo `path` (sin esquema, host, puerto ni query string).
    - Si ya es un path relativo o un token sin URL (ej. la clave de
      agrupación de `_clave_agrupacion_hallazgo` en `api/main.py`, que puede
      ser un nombre de parámetro suelto como `"sid"`), `urlparse` la trata
      como el `path` completo y la devuelve sin cambios -- no hay
      comportamiento nuevo para lo que ya funcionaba.
    - **Rutas SPA con fragmento** (`/#/administration`): `urlparse` parte el
      `#` como fragmento y devuelve `path='/'`, perdiendo la ruta real. El
      ground truth de Juice Shop usa esa convención para el panel de admin
      (VULN-005), y los chequeos dirigidos (`tools/deteccion_dirigida.py`)
      reportan el activo afectado con esa misma ruta. Sin preservar el
      fragmento, un hallazgo de control de acceso en /#/administration se
      colapsaría a `/` y jamás matchearía. Solo se preserva para referencias
      RELATIVAS (sin esquema/host): una URL absoluta que un escáner reporta
      nunca lleva fragmento (el servidor no ve el `#`), así que ese caso sigue
      devolviendo solo el `path` como antes.
    """
    if not ubicacion:
        return ""
    partido = urlparse(str(ubicacion).strip())
    if partido.scheme or partido.netloc:
        # URL absoluta: solo el path (comportamiento original intacto).
        return partido.path
    # Referencia relativa: preservar el fragmento de rutas SPA (drop de query).
    if partido.fragment:
        return f"{partido.path}#{partido.fragment}"
    return partido.path
