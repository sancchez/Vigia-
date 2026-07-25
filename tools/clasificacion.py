"""Clasificación determinista de hallazgos por CLASE (señal vs. ruido).

Problema real que resuelve
--------------------------
Un escaneo baseline de ZAP contra una app mediana produce fácilmente 90+
alertas, y la enorme mayoría son avisos de CONFIGURACIÓN ("CSP Header Not
Set", "Missing Anti-clickjacking Header", "Cookie No HttpOnly Flag") — no
puertas abiertas. Hasta ahora `findings` solo tenía UN eje, `severidad`, así
que un SQLi real y una cabecera faltante se veían casi igual en el panel: el
producto se sentía como un generador de alarmas en vez de una herramienta de
seguridad. Este módulo agrega el SEGUNDO eje, ortogonal a `severidad`:

    severidad  -> qué tan grave sería si se explota   (critical..info)
    clase      -> QUÉ TIPO DE COSA ES                 (ver `CLASES`)

Son ejes distintos a propósito: un CSP faltante puede ser `medium` de
severidad y aun así ser higiene, no una puerta abierta. Nada se borra ni se
esconde — la clase solo decide QUÉ LIDERA el panel y qué se agrupa y colapsa
debajo.

Por qué NO usa un LLM
---------------------
Mismo criterio que `agents/verificacion.py` (determinista, sin LLM) y que
`tools/_shared.py::normalize_severity`: esta clasificación tiene que ser
explicable a un cliente que paga ("¿por qué esto está en el bloque de
higiene?" -> "porque su nombre es 'X-Frame-Options Header Not Set', que está
en esta lista explícita"). Una inferencia de un modelo no es auditable ni
reproducible entre corridas; un `if` sobre datos sí.

Orden de decisión (el primero que matchea gana)
-----------------------------------------------
0. `tipo` de suplantación de marca            -> `amenaza_externa`
1. Confirmado contra Exploit-DB               -> `vulnerabilidad_explotable`
2. Nombres de ruido conocido de ZAP baseline  -> `higiene_configuracion`
3. Patrones de clase de vulnerabilidad real   -> `vulnerabilidad_explotable`
4. Patrones genéricos de higiene              -> `higiene_configuracion`
5. CVE presente en el hallazgo crudo          -> `vulnerabilidad_explotable`
6. CWE declarado por la herramienta           -> según el conjunto que lo tenga
7. Patrones de mero reporte/descubrimiento    -> `informativo`
8. Fallback por severidad (y se loguea)

El paso 1 va antes que los patrones de nombre porque una confirmación real
contra Exploit-DB (hay PoC pública para el CVE que la herramienta reportó) es
la evidencia más fuerte que este sistema puede producir; ningún patrón de
texto debería poder degradarla.

El fallback del paso 8 nunca esconde algo dudoso como higiene: si no se pudo
clasificar y la severidad es critical/high/medium, queda como
`vulnerabilidad_explotable` (visible arriba, que es el lado seguro del error)
y se loguea en `vigia.clasificacion` para poder agregar el patrón faltante —
mismo criterio de "nunca fallar en silencio" que ya usa `normalize_severity`.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_log = logging.getLogger("vigia.clasificacion")

CLASE_EXPLOTABLE = "vulnerabilidad_explotable"
CLASE_AMENAZA_EXTERNA = "amenaza_externa"
CLASE_HIGIENE = "higiene_configuracion"
CLASE_INFORMATIVO = "informativo"

# El orden de esta tupla es el orden de importancia para el panel (de más a
# menos accionable) — `frontend/src/pages/Dashboard.tsx` asume este mismo
# orden al agrupar.
CLASES: tuple[str, ...] = (
    CLASE_EXPLOTABLE,
    CLASE_AMENAZA_EXTERNA,
    CLASE_HIGIENE,
    CLASE_INFORMATIVO,
)

# Clases que cuentan como "riesgo real" para el nivel de riesgo del panel,
# los KPIs y el SLA estricto. Ver `api/main.py::_calcular_sla_due_at`.
CLASES_RIESGO_REAL: frozenset[str] = frozenset({CLASE_EXPLOTABLE, CLASE_AMENAZA_EXTERNA})

# --- Paso 0: suplantación de marca -----------------------------------------
# Estos `tipo` los producen `agents/antisuplantacion.py` (dnstwist/Sherlock
# bajo demanda, vía `_extraer_findings_antisuplantacion`) y
# `api/certstream_listener.py` (vigilancia continua). No son una
# vulnerabilidad EN la infraestructura del cliente ni una mala práctica suya
# — son un tercero haciéndose pasar por él. Colapsarlos dentro de "higiene"
# escondería una alerta de negocio real; llamarlos "vulnerabilidad
# explotable" sería mentir sobre qué son. De ahí la clase propia, que igual
# cuenta como riesgo real (`CLASES_RIESGO_REAL`) y lidera el panel.
_TIPOS_AMENAZA_EXTERNA: frozenset[str] = frozenset(
    {
        "dominio_variante",
        "dominio_variante_certstream",
        "perfil_red_social",
    }
)

# --- Paso 2: ruido conocido de ZAP baseline --------------------------------
# Frases largas y específicas, tomadas de los nombres reales de alerta de ZAP
# (verificadas contra la corrida en vivo contra Juice Shop, ver
# `eval/live_run_report.md`). Se evalúan ANTES que los patrones de
# vulnerabilidad del paso 3 porque son nombres completos y no ambiguos; un
# hallazgo cuyo nombre es exactamente uno de estos es higiene sin importar
# qué otras palabras contenga.
_RUIDO_ZAP: tuple[str, ...] = (
    "absence of anti-csrf tokens",
    "cross-domain misconfiguration",
    "content security policy (csp) header not set",
    "csp: wildcard directive",
    "csp: style-src unsafe-inline",
    "csp: script-src unsafe-inline",
    "missing anti-clickjacking header",
    "x-frame-options header not set",
    "x-frame-options defined via meta tag",
    "x-content-type-options header missing",
    "strict-transport-security header not set",
    "strict-transport-security header",
    "cookie no httponly flag",
    "cookie without httponly flag",
    "cookie without secure flag",
    "cookie without samesite attribute",
    "cookie with samesite attribute none",
    "permissions policy header not set",
    "referrer-policy header not set",
    "re-examine cache-control directives",
    "incomplete or no cache-control",
    "server leaks version information",
    "server leaks information via",
    "x-powered-by header",
    "x-aspnet-version response header",
    "x-debug-token information leak",
    "deprecated feature policy header set",
)

# --- Paso 3: clases de vulnerabilidad realmente explotables -----------------
# Subcadenas (en minúsculas) del nombre/tipo del hallazgo. Cada una nombra una
# clase de ataque con la que alguien puede HACER algo contra el sistema, no
# una buena práctica ausente.
_PATRONES_EXPLOTABLES: tuple[str, ...] = (
    "sql injection",
    "sqli",
    "nosql injection",
    "cross site scripting",
    "cross-site scripting",
    "xss",
    "remote code execution",
    "code injection",
    "command injection",
    "os command",
    "remote command",
    "path traversal",
    "directory traversal",
    "local file inclusion",
    "remote file inclusion",
    "file inclusion",
    "arbitrary file read",
    "arbitrary file write",
    "arbitrary file upload",
    "unrestricted file upload",
    "server side request forgery",
    "server-side request forgery",
    "ssrf",
    "xml external entity",
    "xxe",
    "insecure deserialization",
    "deserialization",
    "server side template injection",
    "server-side template injection",
    "template injection",
    "ssti",
    "ldap injection",
    "xpath injection",
    "expression language injection",
    "crlf injection",
    "http request smuggling",
    "request smuggling",
    "authentication bypass",
    "auth bypass",
    "authorization bypass",
    "privilege escalation",
    "insecure direct object reference",
    "idor",
    "default credential",
    "default password",
    "weak credential",
    "exposed credential",
    "hardcoded credential",
    "buffer overflow",
    "open redirect",
    "session fixation",
    "remote os command",
    "unauthenticated",
    "backup file disclosure",
    "exposed .env",
    "env file",
    "git config",
    ".git/config",
    "database exposure",
    "exposed database",
    "padding oracle",
    "prototype pollution",
    "log4j",
    "log4shell",
    "spring4shell",
    "shellshock",
    "sensitive data exposure",
)

# --- Paso 4: higiene genérica ----------------------------------------------
# Se evalúan DESPUÉS del paso 3 a propósito: si un nombre contiene a la vez
# una palabra de higiene ("cookie") y una clase de ataque real ("XSS"), gana
# el ataque real.
_PATRONES_HIGIENE: tuple[str, ...] = (
    "header not set",
    "header missing",
    "missing header",
    "header is not",
    "header not configured",
    "security header",
    "clickjacking",
    "content security policy",
    "content-security-policy",
    "httponly",
    "http only flag",
    "samesite",
    "secure flag",
    "cache-control",
    "cache control",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
    "feature-policy",
    "hsts",
    "strict-transport-security",
    "weak cipher",
    "deprecated tls",
    "insecure cipher",
    "directory browsing",
    "directory listing",
    "autocomplete",
    "charset mismatch",
    "cookie poisoning",
    "cookie slack detector",
)

# --- Paso 5: puro reporte/descubrimiento ------------------------------------
_PATRONES_INFORMATIVOS: tuple[str, ...] = (
    "technology detected",
    "tech-detect",
    "tech detect",
    "fingerprint",
    "waf detect",
    "waf-detect",
    "suspicious comments",
    "timestamp disclosure",
    "modern web application",
    "user agent fuzzer",
    "user controllable html element",
    "information disclosure - sensitive information in url",
    "loosely scoped cookie",
    "non-storable content",
    "storable content",
    "retrieved from cache",
    "http server response header",
    "openapi",
    "robots.txt",
    "sitemap.xml",
    "dns record",
    "email address",
)

# --- Paso 6: CWE declarado por la herramienta -------------------------------
# Solo se leen campos CWE EXPLÍCITOS (ver `_extraer_cwes`), nunca se escanea
# el JSON completo — un CWE citado de paso en un texto de referencia no debe
# poder reclasificar un hallazgo.
_CWES_EXPLOTABLES: frozenset[int] = frozenset(
    {
        22, 23, 36, 73, 74, 77, 78, 88, 89, 90, 91, 94, 95, 98,
        119, 120, 125, 129, 190, 269, 284, 287, 288, 306, 352, 384,
        400, 434, 444, 470, 502, 552, 601, 611, 639, 640, 664, 787,
        798, 862, 863, 913, 917, 918, 1321, 1336,
    }
)
_CWES_HIGIENE: frozenset[int] = frozenset(
    {16, 311, 319, 326, 327, 525, 565, 614, 693, 1004, 1021, 1275,
     }
)
_CWES_INFORMATIVOS: frozenset[int] = frozenset({200, 209, 497, 548, 615})

_CVE_PATTERN = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)
_CWE_NUM_PATTERN = re.compile(r"(\d{1,5})")


def _texto_del_hallazgo(tipo: str, raw: Any) -> str:
    """Nombre/identificador del hallazgo, en minúsculas, para matchear patrones.

    Combina `tipo` (lo que ya se persiste en `findings.tipo`) con los campos
    de nombre nativos de cada herramienta — `name`/`alert` (ZAP),
    `info.name`/`template-id` (Nuclei), `check_id` (Semgrep) — porque el
    `tipo` de una fila vieja puede ser `"desconocido"` mientras el `raw_json`
    sí trae el nombre real.
    """
    partes = [str(tipo or "")]
    if isinstance(raw, dict):
        for clave in ("name", "alert", "check_id", "template-id", "templateID", "title"):
            valor = raw.get(clave)
            if isinstance(valor, str):
                partes.append(valor)
        info = raw.get("info")
        if isinstance(info, dict):
            for clave in ("name", "description"):
                valor = info.get(clave)
                if isinstance(valor, str):
                    partes.append(valor)
        extra = raw.get("extra")
        if isinstance(extra, dict):
            mensaje = extra.get("message")
            if isinstance(mensaje, str):
                partes.append(mensaje)
        # Hallazgo ya envuelto por `agents/escaneo.py` -> {"raw": {...}}
        anidado = raw.get("raw")
        if isinstance(anidado, dict):
            partes.append(_texto_del_hallazgo("", anidado))
    return " ".join(partes).lower()


def _extraer_cwes(raw: Any) -> set[int]:
    """CWEs declarados EXPLÍCITAMENTE por la herramienta (no escaneo de texto).

    Formatos reales soportados: `cweid` (ZAP, string o int),
    `info.classification.cwe-id` (Nuclei, lista tipo `["cwe-89"]`),
    `extra.metadata.cwe` (Semgrep, lista tipo `["CWE-79: ..."]`).
    """
    if not isinstance(raw, dict):
        return set()

    candidatos: list[Any] = []
    candidatos.append(raw.get("cweid"))
    candidatos.append(raw.get("cwe"))

    info = raw.get("info")
    if isinstance(info, dict):
        clasificacion = info.get("classification")
        if isinstance(clasificacion, dict):
            candidatos.append(clasificacion.get("cwe-id"))

    extra = raw.get("extra")
    if isinstance(extra, dict):
        metadata = extra.get("metadata")
        if isinstance(metadata, dict):
            candidatos.append(metadata.get("cwe"))

    anidado = raw.get("raw")
    if isinstance(anidado, dict):
        cwes = _extraer_cwes(anidado)
    else:
        cwes = set()

    for candidato in candidatos:
        valores = candidato if isinstance(candidato, (list, tuple)) else [candidato]
        for valor in valores:
            if valor in (None, ""):
                continue
            match = _CWE_NUM_PATTERN.search(str(valor))
            if match:
                cwes.add(int(match.group(1)))
    return cwes


def _tiene_cve(raw: Any) -> bool:
    try:
        texto = json.dumps(raw, default=str)
    except (TypeError, ValueError):
        texto = str(raw)
    return bool(_CVE_PATTERN.search(texto))


def clasificar_finding(
    tipo: str,
    raw: Any = None,
    *,
    severidad: str = "medium",
    confirmado: bool = False,
    estado_verificacion: str | None = None,
) -> str:
    """Devuelve una de las 4 clases de `CLASES`. Determinista, sin LLM.

    Args:
        tipo: el valor que va a `findings.tipo` (nombre de alerta de ZAP,
            template de Nuclei, `dominio_variante`, `revision_ia:...`, ...).
        raw: el hallazgo crudo (dict) tal como viaja a `findings.raw_json`.
        severidad: severidad ya normalizada (`tools/_shared.py`), solo se usa
            para el fallback del paso 8.
        confirmado: `True` si `agents/verificacion.py` lo confirmó contra
            Exploit-DB.
        estado_verificacion: idem, el string que produce ese mismo agente.

    Ver el docstring del módulo para el orden completo de decisión.
    """
    tipo_str = str(tipo or "")

    # Paso 0 — suplantación de marca (por `tipo` exacto, no por patrón).
    if tipo_str.strip().lower() in _TIPOS_AMENAZA_EXTERNA:
        return CLASE_AMENAZA_EXTERNA

    # Paso 1 — evidencia más fuerte disponible: PoC pública para un CVE real.
    if confirmado or estado_verificacion == "confirmado_via_exploitdb":
        return CLASE_EXPLOTABLE

    texto = _texto_del_hallazgo(tipo_str, raw)

    # Paso 2 — nombres completos de ruido conocido de ZAP baseline.
    for patron in _RUIDO_ZAP:
        if patron in texto:
            return CLASE_HIGIENE

    # Paso 3 — clases de ataque reales.
    for patron in _PATRONES_EXPLOTABLES:
        if patron in texto:
            return CLASE_EXPLOTABLE

    # Paso 4 — higiene genérica.
    for patron in _PATRONES_HIGIENE:
        if patron in texto:
            return CLASE_HIGIENE

    # Paso 5 — un CVE citado identifica una vulnerabilidad concreta en un
    # componente, no una buena práctica ausente. Va ANTES de los patrones
    # informativos porque muchas plantillas de Nuclei se llaman literalmente
    # "CVE-2021-44228 ... Detection": el nombre suena a descubrimiento, pero
    # lo que reporta es una vulnerabilidad conocida en un software real.
    if _tiene_cve(raw) or _CVE_PATTERN.search(tipo_str):
        return CLASE_EXPLOTABLE

    # Paso 6 — CWE declarado por la herramienta.
    cwes = _extraer_cwes(raw)
    if cwes & _CWES_EXPLOTABLES:
        return CLASE_EXPLOTABLE
    if cwes & _CWES_HIGIENE:
        return CLASE_HIGIENE
    if cwes & _CWES_INFORMATIVOS:
        return CLASE_INFORMATIVO

    # Paso 7 — mero reporte/descubrimiento sin CVE ni CWE accionable.
    for patron in _PATRONES_INFORMATIVOS:
        if patron in texto:
            return CLASE_INFORMATIVO

    # Paso 8 — fallback explícito y logueado (nunca silencioso).
    if severidad in ("low", "info"):
        clase = CLASE_INFORMATIVO
    else:
        clase = CLASE_EXPLOTABLE
    _log.warning(
        "clase no determinable por patrón/CWE/CVE para tipo=%r (severidad=%s); "
        "fallback -> %s. Considerá agregar el patrón a tools/clasificacion.py",
        tipo_str[:120],
        severidad,
        clase,
    )
    return clase


def clasificar_fila_finding(fila: dict) -> str:
    """Clasifica una fila YA persistida de `findings` (derivación en lectura).

    Existe para los hallazgos históricos: la columna `findings.clase` se
    agregó de forma aditiva y nullable (ver `db/connection.py`,
    `_FINDINGS_CLASE_COLUMNS`), así que las filas guardadas antes de esta
    migración tienen `clase IS NULL`. En vez de un backfill destructivo que
    congele una decisión sobre datos viejos, los endpoints derivan la clase
    al leer con esta función — mismo criterio determinista, mismo resultado
    que si se hubiera calculado al insertar.
    """
    clase_guardada = fila.get("clase")
    if clase_guardada in CLASES:
        return clase_guardada

    raw: Any = None
    raw_json = fila.get("raw_json")
    if raw_json:
        try:
            raw = json.loads(raw_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = None

    estado_verificacion = fila.get("estado_verificacion")
    if estado_verificacion is None and isinstance(raw, dict):
        valor = raw.get("estado_verificacion")
        estado_verificacion = valor if isinstance(valor, str) else None

    return clasificar_finding(
        fila.get("tipo") or "",
        raw,
        severidad=fila.get("severidad") or "medium",
        confirmado=bool(fila.get("confirmado")),
        estado_verificacion=estado_verificacion,
    )
