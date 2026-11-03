"""Triage DETERMINISTA de un hallazgo crudo: validez, evidencia y urgencia base.

Por qué existe (falso verde medido, corrida real contra Juice Shop)
-------------------------------------------------------------------
`agents/verificacion.py` es determinista por diseño y su único criterio de
CONFIRMACIÓN es cruzar el CVE del hallazgo contra el índice offline de
Exploit-DB. Eso está bien y no cambia. El problema medido fue el eslabón
siguiente: `agents/priorizacion.py` filtraba por `confirmado=True`, así que
toda una clase de vulnerabilidades REALES que por naturaleza NO tienen CVE
(cabeceras de seguridad ausentes, CORS abierto, cookies sin flags, TLS débil,
fuga de información) moría ahí en silencio.

Trace real de la corrida contra OWASP Juice Shop (target de laboratorio, con
autorización firmada):

    escaneo      -> "10 hallazgos crudos sobre 1 objetivo(s)"
    verificacion -> "0/10 confirmados via Exploit-DB"
    priorizacion -> "sin hallazgos confirmados que priorizar"
    remediacion  -> "sin hallazgos priorizados, nada que remediar todavia"

Los 10 hallazgos eran reales ("Content Security Policy (CSP) Header Not Set"
con 5 instancias, "Cross-Domain Misconfiguration" con
`Access-Control-Allow-Origin: *`, ...). El cliente habría recibido un reporte
con remediación vacía teniendo esas 10 cosas encima: un falso verde.

La distinción que faltaba
-------------------------
No son lo mismo, y hasta ahora se trataban igual (ambos fuera del flujo):

    "confirmado por exploit público"   -> hay PoC pública para un CVE real
    "hallazgo válido sin CVE aplicable" -> ZAP/Nuclei YA hizo la detección

Un hallazgo de ZAP trae `riskcode` y `confidence` PROPIOS: eso ES evidencia.
El CVE con exploit público es un AGRAVANTE de urgencia, no el requisito de
entrada al pipeline. Este módulo formaliza esa distinción sin inventar nada:

- `estado_validez`: `valido` | `falso_positivo_declarado`. Lo segundo es
  EXCLUSIVAMENTE `confidence: "0"` de ZAP, que en el vocabulario de la propia
  herramienta significa "False Positive". Nadie más declara eso, así que
  nadie más se descarta.
- `severidad`: la que ya calcula `tools/_shared.py::normalize_severity` a
  partir del `riskcode`/`severity` NATIVO de la herramienta. No se infla ni
  se recalibra: si ZAP dice `riskcode 0`, es `info` y se presenta como
  informativo, no como urgente.
- `urgencia_base`: techo determinista de urgencia derivado de la severidad,
  con UN escalón de agravante si hay exploit público. Es un TECHO: el LLM de
  priorización puede bajarlo con contexto de negocio, nunca subirlo. Un
  hallazgo no puede volverse urgente porque un modelo "sonó convincente" ni
  porque convenga que el producto muestre algo.

Sin LLM, igual que `agents/verificacion.py`, `tools/clasificacion.py` y
`tools/takedown.py`: cada decisión de acá tiene que poder explicársele a un
cliente que paga ("¿por qué esto es medio?" -> "porque ZAP lo reportó con
riskcode 2 y así se mapea"), y una inferencia de un modelo no es auditable.
"""

from __future__ import annotations

import html
import re
from typing import Any

from tools._shared import normalize_severity
from tools.clasificacion import clasificar_finding

# --- Validez ---------------------------------------------------------------

VALIDO = "valido"
"""El hallazgo tiene evidencia propia de la herramienta y entra al flujo."""

FALSO_POSITIVO_DECLARADO = "falso_positivo_declarado"
"""La PROPIA herramienta lo declaró falso positivo (ZAP `confidence: "0"`).
Nunca se presenta al cliente como hallazgo activo. No se borra: queda en
`verified_findings` con este estado para que la corrida siga siendo
auditable (mismo criterio de "no se descarta en silencio" que ya usa
`agents/verificacion.py`)."""

ESTADOS_VALIDEZ: tuple[str, ...] = (VALIDO, FALSO_POSITIVO_DECLARADO)

# ZAP: `confidence` es "0".."4" -- 0 = False Positive, 1 = Low, 2 = Medium,
# 3 = High, 4 = User Confirmed. Solo el 0 es una declaración de la
# herramienta de que ELLA MISMA cree que se equivocó.
_ZAP_CONFIDENCE_FALSO_POSITIVO = "0"

# --- Urgencia --------------------------------------------------------------

URGENCIA_ALTA = "alta"
URGENCIA_MEDIA = "media"
URGENCIA_BAJA = "baja"
URGENCIA_INFORMATIVA = "informativa"

# Menor número = más urgente (mismo criterio de orden que
# `eval/agent_quality/checks.py::_RANGO_SEVERIDAD`).
ORDEN_URGENCIA: dict[str, int] = {
    URGENCIA_ALTA: 0,
    URGENCIA_MEDIA: 1,
    URGENCIA_BAJA: 2,
    URGENCIA_INFORMATIVA: 3,
}

ORDEN_SEVERIDAD: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

# Mapa severidad canónica -> urgencia base. `info` NO comparte urgencia con
# `low`: un hallazgo informativo tiene que llegar al reporte, pero rotulado
# como informativo, nunca con la misma urgencia que un riesgo real.
_URGENCIA_POR_SEVERIDAD: dict[str, str] = {
    "critical": URGENCIA_ALTA,
    "high": URGENCIA_ALTA,
    "medium": URGENCIA_MEDIA,
    "low": URGENCIA_BAJA,
    "info": URGENCIA_INFORMATIVA,
}

# Un escalón de agravante cuando hay PoC pública para el CVE reportado.
# `informativa` NO escala: si la herramienta lo reportó como informativo,
# subirlo a "baja" por un CVE citado de paso sería exactamente el tipo de
# inflado que este módulo existe para evitar.
_ESCALA_POR_EXPLOIT: dict[str, str] = {
    URGENCIA_MEDIA: URGENCIA_ALTA,
    URGENCIA_BAJA: URGENCIA_MEDIA,
}


def urgencia_base(severidad: str, exploit_publico: bool = False) -> str:
    """Techo determinista de urgencia de negocio para un hallazgo.

    Deriva SOLO de la severidad nativa ya normalizada y de si existe un
    exploit público confirmado contra Exploit-DB. No mira contexto de
    negocio (eso es lo único que el LLM de priorización aporta, y solo puede
    usarlo para BAJAR esta urgencia, nunca para subirla).
    """
    base = _URGENCIA_POR_SEVERIDAD.get(str(severidad or "").strip().lower(), URGENCIA_MEDIA)
    if exploit_publico:
        return _ESCALA_POR_EXPLOIT.get(base, base)
    return base


def acotar_urgencia(propuesta: Any, techo: str) -> str:
    """Devuelve `propuesta` si es una urgencia válida y NO más urgente que
    `techo`; si no, devuelve `techo`.

    Regla de calibración explícita del producto: la severidad la deciden las
    herramientas (datos), no el modelo. El LLM puede decir "esto es menos
    urgente de lo que parece porque ese sistema no está expuesto" (bajar),
    nunca "esto en realidad es crítico" (subir).
    """
    candidata = str(propuesta or "").strip().lower()
    if candidata not in ORDEN_URGENCIA:
        return techo
    if ORDEN_URGENCIA[candidata] < ORDEN_URGENCIA.get(techo, ORDEN_URGENCIA[URGENCIA_MEDIA]):
        return techo
    return candidata


# --- Identidad y evidencia del hallazgo ------------------------------------

_HERRAMIENTAS_ZAP = ("zap", "zap-baseline", "zap-full-scan")


def inferir_herramienta(raw: Any) -> str:
    """Deduce qué herramienta produjo `raw` por la FORMA del dict.

    Necesario porque `verificar_un_hallazgo()` se usa en dos caminos: dentro
    del grafo, donde el hallazgo viene envuelto (`{"objetivo", "herramienta",
    "raw"}`) y la herramienta es explícita; y en el escaneo activo
    checkpointed (`api/main.py`), donde llega directo el elemento crudo de
    `tools/zap_api.py::get_alerts()` sin envoltorio.

    Devuelve "" si no puede decidirlo -- nunca adivina: quien llama trata ese
    caso como herramienta desconocida (`normalize_severity` ya loguea y usa
    "medium" como default seguro).
    """
    if not isinstance(raw, dict):
        return ""
    if "template-id" in raw or "templateID" in raw or "matched-at" in raw:
        return "nuclei"
    if "pluginid" in raw or "pluginId" in raw or "riskcode" in raw or "riskdesc" in raw:
        return "zap"
    if "risk" in raw and ("alert" in raw or "name" in raw):
        return "zap"
    if "check_id" in raw and "extra" in raw:
        return "semgrep"
    if "VulnerabilityID" in raw or ("Severity" in raw and "PkgName" in raw):
        return "trivy"
    if isinstance(raw.get("vulnerability"), dict):
        return "grype"
    return ""


def nombre_hallazgo(raw: Any, herramienta: str = "") -> str:
    """Nombre humano del hallazgo -- lo que se persiste en `findings.tipo`.

    Antes de este módulo, `api/main.py::scan()` leía `hallazgo.get("tipo")`
    sobre la salida de `agents/verificacion.py`, que NUNCA setea esa clave:
    los 10 hallazgos reales de la corrida contra Juice Shop se habrían
    persistido con `tipo = "desconocido"` (mismo patrón de bug que el ya
    documentado para `severidad` en `tools/_shared.py`). El nombre real vive
    dentro de `raw`, con nombre de campo propio de cada herramienta.
    """
    if not isinstance(raw, dict):
        return "desconocido"
    tool = (herramienta or inferir_herramienta(raw)).strip().lower()
    if tool == "nuclei":
        info = raw.get("info")
        if isinstance(info, dict) and info.get("name"):
            return str(info["name"])
        return str(raw.get("template-id") or raw.get("templateID") or "desconocido")
    if tool == "semgrep":
        return str(raw.get("check_id") or "desconocido")
    if tool == "trivy":
        return str(raw.get("VulnerabilityID") or raw.get("Title") or "desconocido")
    if tool == "grype":
        vuln = raw.get("vulnerability")
        if isinstance(vuln, dict) and vuln.get("id"):
            return str(vuln["id"])
        return "desconocido"
    # ZAP (ambas formas) y default.
    for clave in ("name", "alert", "title"):
        valor = raw.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return "desconocido"


def _primera_instancia(raw: dict) -> dict:
    instancias = raw.get("instances")
    if isinstance(instancias, list) and instancias and isinstance(instancias[0], dict):
        return instancias[0]
    return {}


def ubicacion_hallazgo(raw: Any, herramienta: str = "", objetivo: str = "") -> str:
    """URI concreta afectada -- la evidencia que un cliente puede ir a mirar.

    ZAP baseline (`zap-baseline.py`, el camino de `POST /scan`) devuelve
    `instances: [{uri, method, param, evidence}]`; la API real de ZAP
    (`tools/zap_api.py::get_alerts()`, el camino de `POST /scan/activo`)
    devuelve `url` de nivel superior. Nuclei usa `matched-at`. Cae a
    `objetivo` (el dominio escaneado) solo si nada de eso está.
    """
    if not isinstance(raw, dict):
        return objetivo or ""
    tool = (herramienta or inferir_herramienta(raw)).strip().lower()
    if tool == "nuclei":
        return str(raw.get("matched-at") or raw.get("host") or objetivo or "")
    if tool == "semgrep":
        return str(raw.get("path") or objetivo or "")
    if tool in _HERRAMIENTAS_ZAP or not tool:
        directo = raw.get("url") or raw.get("uri")
        if directo:
            return str(directo)
        instancia = _primera_instancia(raw)
        if instancia.get("uri"):
            return str(instancia["uri"])
    return objetivo or ""


def parametro_afectado(raw: Any) -> str:
    """Parámetro/cabecera concreta señalada por la herramienta, si la hay."""
    if not isinstance(raw, dict):
        return ""
    directo = raw.get("param")
    if isinstance(directo, str) and directo.strip():
        return directo.strip()
    instancia = _primera_instancia(raw)
    valor = instancia.get("param")
    return valor.strip() if isinstance(valor, str) else ""


def evidencia_hallazgo(raw: Any) -> str:
    """Fragmento textual que la herramienta señala como prueba.

    Para "Cross-Domain Misconfiguration" de la corrida real, esto es
    literalmente `Access-Control-Allow-Origin: *` -- el dato que hace que el
    hallazgo sea verificable a mano por el cliente, sin creerle a nadie.
    """
    if not isinstance(raw, dict):
        return ""
    directo = raw.get("evidence")
    if isinstance(directo, str) and directo.strip():
        return directo.strip()
    instancia = _primera_instancia(raw)
    valor = instancia.get("evidence")
    return valor.strip() if isinstance(valor, str) else ""


# La `evidence` de ZAP puede ser un bloque entero de HTML/JS: en la corrida
# real contra Juice Shop, la de "Modern Web Application" era un `<script>` de
# 15 líneas. Eso es útil como dato crudo (viaja completo en `raw_json`), pero
# ilegible dentro de una viñeta del reporte del cliente.
_LIMITE_EVIDENCIA = 180


def evidencia_corta(evidencia: Any, limite: int = _LIMITE_EVIDENCIA) -> str:
    """Evidencia en una sola línea, recortada para mostrar. Nunca la altera
    en la base: solo la forma en que se RENDERIZA en el documento."""
    texto = " ".join(str(evidencia or "").split())
    if len(texto) <= limite:
        return texto
    return texto[:limite].rstrip() + "… (recortado; completo en el detalle del hallazgo)"


def instancias_afectadas(raw: Any) -> int:
    """Cuántas URIs distintas afecta el mismo hallazgo (ZAP: `count`)."""
    if not isinstance(raw, dict):
        return 1
    for clave in ("count", "instances_count"):
        valor = raw.get(clave)
        if isinstance(valor, (int, str)):
            try:
                n = int(valor)
            except (TypeError, ValueError):
                continue
            if n > 0:
                return n
    instancias = raw.get("instances")
    if isinstance(instancias, list) and instancias:
        return len(instancias)
    return 1


_TAG_HTML = re.compile(r"<[^>]+>")


def texto_plano(valor: Any) -> str:
    """Limpia el HTML que ZAP mete en `desc`/`solution` (`<p>`, `<br/>`).

    ZAP entrega esos campos con marcado HTML embebido; el reporte del cliente
    es markdown, así que el marcado se quita acá en vez de filtrarse a la
    salida.
    """
    if not isinstance(valor, str) or not valor.strip():
        return ""
    limpio = _TAG_HTML.sub(" ", valor)
    limpio = html.unescape(limpio)
    return " ".join(limpio.split())


def solucion_de_la_herramienta(raw: Any) -> str:
    """Remediación que la PROPIA herramienta documenta para el hallazgo.

    ZAP baseline trae `solution` por alerta; Nuclei trae `info.remediation`.
    Es texto escrito por los mantenedores de la herramienta para ese chequeo
    exacto -- más confiable, y desde luego más honesto, que hacer que un LLM
    lo redacte de cero cuando el LLM no está disponible.
    """
    if not isinstance(raw, dict):
        return ""
    directo = texto_plano(raw.get("solution"))
    if directo:
        return directo
    info = raw.get("info")
    if isinstance(info, dict):
        return texto_plano(info.get("remediation"))
    return ""


def descripcion_de_la_herramienta(raw: Any) -> str:
    """Descripción que la herramienta da del problema (`desc`/`info.description`)."""
    if not isinstance(raw, dict):
        return ""
    directo = texto_plano(raw.get("desc") or raw.get("description"))
    if directo:
        return directo
    info = raw.get("info")
    if isinstance(info, dict):
        return texto_plano(info.get("description"))
    return ""


def es_falso_positivo_declarado(raw: Any, herramienta: str = "") -> bool:
    """`True` solo si la herramienta declara ELLA MISMA que es falso positivo.

    Hoy solo ZAP lo expresa: `confidence: "0"` == "False Positive" en su
    vocabulario. Nuclei/Trivy/Grype/Semgrep no tienen equivalente, así que
    nada de esas herramientas se descarta por este camino -- descartar por
    heurística propia sería inventarse un criterio.
    """
    if not isinstance(raw, dict):
        return False
    tool = (herramienta or inferir_herramienta(raw)).strip().lower()
    if tool not in _HERRAMIENTAS_ZAP:
        return False
    confianza = raw.get("confidence")
    if confianza is None:
        return False
    return str(confianza).strip() == _ZAP_CONFIDENCE_FALSO_POSITIVO


def triage_hallazgo(hallazgo: dict, exploit_publico: bool = False) -> dict:
    """Campos de triage determinista para un hallazgo (crudo o ya verificado).

    Devuelve SOLO los campos nuevos (quien llama los fusiona), para que este
    módulo no pueda pisar por accidente nada que ya haya puesto
    `agents/verificacion.py`.

    Acepta las dos formas que circulan por el sistema: el hallazgo envuelto
    por `agents/escaneo.py` (`{"objetivo", "herramienta", "raw"}`) y el
    elemento crudo del escaneo activo checkpointed.
    """
    raw = hallazgo.get("raw") if isinstance(hallazgo.get("raw"), dict) else hallazgo
    herramienta = str(hallazgo.get("herramienta") or "").strip() or inferir_herramienta(raw)
    objetivo = str(hallazgo.get("objetivo") or "")

    falso_positivo = es_falso_positivo_declarado(raw, herramienta)
    severidad = normalize_severity(raw, herramienta)
    tipo = nombre_hallazgo(raw, herramienta)
    clase = clasificar_finding(
        tipo,
        raw,
        severidad=severidad,
        confirmado=bool(exploit_publico),
    )

    return {
        "tipo": tipo,
        "herramienta": herramienta or "desconocida",
        "severidad": severidad,
        "clase": clase,
        "endpoint": ubicacion_hallazgo(raw, herramienta, objetivo),
        "parametro": parametro_afectado(raw),
        "evidencia": evidencia_hallazgo(raw),
        "instancias": instancias_afectadas(raw),
        "descripcion": descripcion_de_la_herramienta(raw),
        "solucion_herramienta": solucion_de_la_herramienta(raw),
        "exploit_publico": bool(exploit_publico),
        "estado_validez": FALSO_POSITIVO_DECLARADO if falso_positivo else VALIDO,
        "urgencia_base": (
            URGENCIA_INFORMATIVA
            if falso_positivo
            else urgencia_base(severidad, bool(exploit_publico))
        ),
    }


def es_valido(hallazgo: dict) -> bool:
    """`True` si el hallazgo debe seguir al resto del pipeline.

    Un hallazgo SIN `estado_validez` (formas viejas, hallazgos construidos a
    mano en tests, entradas de otros módulos) se considera VÁLIDO: el sesgo
    seguro acá es dejar pasar y que se vea, no descartar en silencio -- que
    es justo el bug que este módulo cierra.
    """
    return hallazgo.get("estado_validez", VALIDO) != FALSO_POSITIVO_DECLARADO


def ordenar_por_riesgo(hallazgos: list[dict]) -> list[dict]:
    """Orden determinista: severidad primero, exploit público como desempate.

    `eval/agent_quality/checks.py::validar_orden_por_severidad` exige que la
    salida de priorización no tenga una entrada más urgente que la anterior;
    este orden lo garantiza sin depender de que el LLM devuelva la lista bien
    ordenada.
    """
    return sorted(
        hallazgos,
        key=lambda h: (
            ORDEN_SEVERIDAD.get(str(h.get("severidad", "")).strip().lower(), 99),
            0 if h.get("exploit_publico") else 1,
            str(h.get("tipo", "")),
        ),
    )
