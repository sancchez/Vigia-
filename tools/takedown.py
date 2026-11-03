"""Borrador de solicitud de eliminación (takedown) DETERMINISTA por dominio clon.

Por qué existe
--------------
`agents/antisuplantacion.py` le pide a un LLM que redacte el borrador de
takedown, y guarda el resultado en `analisis_claude`. Eso tiene dos problemas
para un cliente real:

1. **Depende de una API key.** Si el despliegue no tiene `ANTHROPIC_API_KEY`
   ni `OPENAI_API_KEY` (`agents/_llm.py::LLMNoDisponibleError`), el campo queda
   en `None` y el panel "Protección de marca" muestra un hallazgo sin ninguna
   acción posible. Un cliente sin LLM configurado tiene que recibir un
   borrador USABLE, no un campo vacío.
2. **Es un solo texto para todo el lote.** `analisis_claude` es un análisis del
   conjunto de señales, no una carta por dominio. El panel lo muestra bajo el
   rótulo "Borrador de takedown" en CADA fila -- el mismo texto repetido N
   veces, sin el registrador ni el contacto de abuso del dominio de esa fila,
   que es exactamente el dato que hace falta para enviarlo.

Este módulo produce, por cada dominio clon, un borrador con datos REALES
recolectados en el mismo pipeline: registrador y contacto de abuso vía RDAP
(el sucesor de WHOIS, JSON y sin scraping), fecha de registro, evidencia DNS
ya recolectada por dnstwist (registros A, nameservers, MX) y el scoring de
infraestructura activa de `tools/antisuplantacion.py`. Nada se inventa: un
dato que RDAP no devuelve simplemente no aparece en la carta, y el borrador
lo dice explícitamente donde corresponde ("no publicado por el registrador")
en vez de rellenar con un placeholder.

El borrador del LLM, cuando SÍ hay LLM configurado, no se pierde: viaja igual
en `findings.raw_json["analisis_claude"]` y `api/main.py` lo usa como
respaldo. Ver `api/main.py::_dominio_clon_out_de_row`.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("vigia.takedown")

# Bootstrap de RDAP: rdap.org redirige a la base RDAP autoritativa del TLD
# (IANA mantiene el mapa). Un solo endpoint en vez de hardcodear la URL de
# cada registro -- configurable por si un despliegue prefiere apuntar directo
# a un servidor RDAP propio o a un mirror.
RDAP_BOOTSTRAP_URL = "https://rdap.org/domain/{dominio}"

_CACHE_RDAP: dict[str, dict] = {}

# Nombres legibles de las cuatro señales de `tools/antisuplantacion.py::InfraScore`.
# La carta va dirigida a un humano del equipo de abuso de un registrador, no a
# otro sistema: "correo (MX)" se entiende sin abrir la documentación, "mx" no.
_ETIQUETA_SEÑAL: dict[str, str] = {
    "dns_a": "resolución DNS",
    "mx": "servidores de correo (MX)",
    "ssl": "certificado TLS en el puerto 443",
    "http": "sitio web respondiendo",
}


def _vcard_a_dict(vcard_array: Any) -> dict[str, str]:
    """Aplana un `vcardArray` de RDAP (jCard, RFC 7095) a {campo: valor}.

    Formato real: `["vcard", [["version",{},"text","4.0"], ["fn",{},"text","Registrador SA"],
    ["email",{},"text","abuse@registrador.com"], ...]]`. Se toma el PRIMER
    valor de cada campo (RDAP puede repetir `email` para roles distintos; el
    contacto de abuso viene en su propia entidad, así que dentro de esa
    entidad el primero es el correcto).
    """
    campos: dict[str, str] = {}
    if not isinstance(vcard_array, list) or len(vcard_array) < 2:
        return campos
    for entrada in vcard_array[1] or []:
        if not isinstance(entrada, list) or len(entrada) < 4:
            continue
        nombre, valor = entrada[0], entrada[3]
        if isinstance(nombre, str) and isinstance(valor, str) and nombre not in campos:
            campos[nombre] = valor
    return campos


def _buscar_entidad(entidades: Any, rol: str) -> Optional[dict]:
    """Primera entidad (recursivamente, RDAP anida el contacto de abuso dentro
    del registrador) cuyo `roles` incluya `rol`."""
    if not isinstance(entidades, list):
        return None
    for entidad in entidades:
        if not isinstance(entidad, dict):
            continue
        if rol in (entidad.get("roles") or []):
            return entidad
        anidada = _buscar_entidad(entidad.get("entities"), rol)
        if anidada is not None:
            return anidada
    return None


def _fecha_evento(eventos: Any, accion: str) -> Optional[str]:
    if not isinstance(eventos, list):
        return None
    for evento in eventos:
        if isinstance(evento, dict) and evento.get("eventAction") == accion:
            fecha = evento.get("eventDate")
            return fecha if isinstance(fecha, str) else None
    return None


def consultar_rdap(dominio: str, timeout: int = 10) -> dict:
    """Datos de registro públicos de `dominio` vía RDAP -- best-effort.

    Devuelve `{}` (nunca lanza) si el dominio no está en RDAP, el TLD no
    tiene servidor RDAP, la red falla, o la respuesta no es JSON válido:
    mismo patrón de degradación con gracia que
    `tools/antisuplantacion.py::capturar_screenshot`. El borrador de takedown
    se genera igual sin estos datos, solo que sin destinatario sugerido.

    Claves posibles: `registrador`, `registrador_url`, `abuse_email`,
    `abuse_telefono`, `registrado_en`, `nameservers`, `estados`.

    NOTA: RDAP se consulta contra el registro/registrador (rdap.org y sus
    redirecciones), NO contra la infraestructura del dominio clon -- por eso
    no pasa por `turno_de_sondeo`: no es un sondeo al host de un tercero, es
    una consulta a un servicio público de directorio, diseñado para ser
    consultado. Se cachea por proceso para no repetir la misma consulta al
    reprocesar el mismo dominio.
    """
    dominio = (dominio or "").strip().lower()
    if not dominio:
        return {}
    if dominio in _CACHE_RDAP:
        return _CACHE_RDAP[dominio]

    datos: dict = {}
    try:
        peticion = urllib.request.Request(
            RDAP_BOOTSTRAP_URL.format(dominio=dominio),
            headers={"Accept": "application/rdap+json, application/json"},
        )
        with urllib.request.urlopen(peticion, timeout=timeout) as resp:  # noqa: S310 -- URL fija de RDAP
            cuerpo = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError, ValueError):
        _CACHE_RDAP[dominio] = {}
        return {}
    except Exception:  # noqa: BLE001 -- RDAP es enriquecimiento, jamás debe tumbar el pipeline
        logger.exception("Consulta RDAP fallida para %s", dominio)
        _CACHE_RDAP[dominio] = {}
        return {}

    if not isinstance(cuerpo, dict):
        _CACHE_RDAP[dominio] = {}
        return {}

    entidades = cuerpo.get("entities")
    registrador = _buscar_entidad(entidades, "registrar")
    if registrador:
        vcard = _vcard_a_dict(registrador.get("vcardArray"))
        if vcard.get("fn"):
            datos["registrador"] = vcard["fn"]
        for enlace in registrador.get("links") or []:
            if isinstance(enlace, dict) and enlace.get("value"):
                datos["registrador_url"] = enlace.get("href") or enlace.get("value")
                break

    abuso = _buscar_entidad(entidades, "abuse")
    if abuso:
        vcard_abuso = _vcard_a_dict(abuso.get("vcardArray"))
        if vcard_abuso.get("email"):
            datos["abuse_email"] = vcard_abuso["email"]
        if vcard_abuso.get("tel"):
            # jCard entrega el teléfono como URI (`tel:+1.2086851750`,
            # confirmado contra una respuesta RDAP real). El prefijo no aporta
            # nada en una carta dirigida a una persona.
            datos["abuse_telefono"] = vcard_abuso["tel"].removeprefix("tel:")

    registrado_en = _fecha_evento(cuerpo.get("events"), "registration")
    if registrado_en:
        datos["registrado_en"] = registrado_en

    nameservers = [
        ns.get("ldhName")
        for ns in (cuerpo.get("nameservers") or [])
        if isinstance(ns, dict) and ns.get("ldhName")
    ]
    if nameservers:
        datos["nameservers"] = [n.lower() for n in nameservers]

    estados = [e for e in (cuerpo.get("status") or []) if isinstance(e, str)]
    if estados:
        datos["estados"] = estados

    _CACHE_RDAP[dominio] = datos
    return datos


def _lista_o_no_publicado(valores: Any, vacio: str) -> str:
    if isinstance(valores, list) and valores:
        return ", ".join(str(v) for v in valores)
    return vacio


def generar_borrador_takedown(
    *,
    dominio_clon: str,
    dominio_legitimo: str,
    señal: Optional[dict] = None,
    rdap: Optional[dict] = None,
    generado_en: Optional[str] = None,
) -> str:
    """Carta de takedown lista para enviar, armada solo con datos verificables.

    `señal` es la señal cruda de `agents/antisuplantacion.py::node()` ya
    enriquecida por `api/main.py::_enriquecer_dominios_clon` (claves
    `dns_a`, `dns_ns`, `tiene_correo`, `scoring`, `score`, `ssl_not_before`).
    `rdap` es la salida de `consultar_rdap`. Ambas opcionales: con menos
    datos la carta es más corta, nunca inventa.

    El texto es intencionalmente sobrio y factual -- va dirigido al equipo de
    abuso de un registrador, que recibe cientos de estas al día y descarta
    las que no traen evidencia concreta. No incluye afirmaciones que este
    sistema no puede sostener (ej. "el sitio distribuye malware"): solo lo
    medido.
    """
    señal = señal or {}
    rdap = rdap or {}
    generado_en = generado_en or datetime.now(timezone.utc).isoformat(timespec="seconds")

    marca = (dominio_legitimo or "").split(".")[0] or dominio_legitimo
    scoring = señal.get("scoring") if isinstance(señal.get("scoring"), dict) else {}
    score = señal.get("score") if isinstance(señal.get("score"), int) else 0

    destinatario = rdap.get("abuse_email")
    registrador = rdap.get("registrador")

    lineas: list[str] = []
    lineas.append(
        f"Para: {destinatario}"
        if destinatario
        else "Para: (contacto de abuso no publicado por el registrador — usar el formulario "
        "de abuso de su sitio web)"
    )
    if rdap.get("abuse_telefono"):
        lineas.append(f"Contacto alterno de abuso (RDAP): {rdap['abuse_telefono']}")
    lineas.append(
        f"Asunto: Solicitud de suspensión por suplantación de marca — {dominio_clon}"
    )
    lineas.append("")
    lineas.append(
        f"Estimado equipo de abuso{f' de {registrador}' if registrador else ''}:"
    )
    lineas.append("")
    lineas.append(
        f"Escribimos en representación del titular del dominio {dominio_legitimo} y de la "
        f"marca «{marca}». Hemos detectado el dominio {dominio_clon}, una variante "
        f"tipográfica de nuestro dominio legítimo, registrada por un tercero sin relación "
        f"con el titular."
    )
    lineas.append("")
    lineas.append("EVIDENCIA TÉCNICA VERIFICABLE")
    lineas.append("")
    lineas.append(f"- Dominio reportado: {dominio_clon}")
    lineas.append(f"- Dominio legítimo del titular: {dominio_legitimo}")
    if señal.get("fuzzer"):
        lineas.append(
            f"- Tipo de permutación detectada: {señal['fuzzer']} "
            "(clasificación de dnstwist, herramienta pública de detección de typosquatting)"
        )
    if rdap.get("registrado_en"):
        lineas.append(f"- Fecha de registro (RDAP): {rdap['registrado_en']}")
    if registrador:
        lineas.append(f"- Registrador (RDAP): {registrador}")
    lineas.append(
        f"- Registros A: {_lista_o_no_publicado(señal.get('dns_a'), 'sin registros A publicados')}"
    )
    lineas.append(
        "- Nameservers: "
        f"{_lista_o_no_publicado(señal.get('dns_ns') or rdap.get('nameservers'), 'no publicados')}"
    )
    if rdap.get("estados"):
        lineas.append(f"- Estados EPP (RDAP): {', '.join(rdap['estados'])}")
    if señal.get("ssl_not_before"):
        lineas.append(
            f"- Certificado TLS emitido (NotBefore): {señal['ssl_not_before']} "
            "— el dominio presenta un certificado activo en el puerto 443"
        )
    if scoring:
        activas = [_ETIQUETA_SEÑAL.get(nombre, nombre) for nombre, activo in scoring.items() if activo]
        lineas.append(
            f"- Infraestructura activa comprobada ({score}/4): "
            + (", ".join(activas) if activas else "ninguna señal activa al momento de la verificación")
        )
    if señal.get("tiene_correo"):
        lineas.append(
            "- **El dominio tiene servidores de correo (MX) configurados**, por lo que puede "
            "emitir correo electrónico que aparente provenir de nuestra organización. Este es "
            "el vector de mayor riesgo inmediato para nuestros clientes."
        )
    lineas.append(f"- Verificación realizada el: {generado_en} (UTC)")
    lineas.append("")
    lineas.append("SOLICITUD")
    lineas.append("")
    lineas.append(
        f"Solicitamos la suspensión del dominio {dominio_clon} conforme a su política de uso "
        "aceptable y a las obligaciones del registrador bajo el acuerdo de acreditación "
        "ICANN en materia de abuso de registro. Subsidiariamente, solicitamos que se nos "
        "informe el procedimiento formal aplicable y los datos de contacto del registrante "
        "que la normativa vigente permita divulgar."
    )
    lineas.append("")
    lineas.append(
        "Quedamos a disposición para aportar documentación adicional que acredite la "
        "titularidad de la marca y del dominio legítimo."
    )
    lineas.append("")
    lineas.append("Atentamente,")
    lineas.append(f"[Nombre y cargo del responsable] — titular de {dominio_legitimo}")
    lineas.append("[Correo de contacto] · [Teléfono]")
    lineas.append("")
    lineas.append(
        "---",
    )
    lineas.append(
        "Evidencia recolectada automáticamente por Vigia a partir de registros públicos "
        "(DNS, RDAP, Certificate Transparency). Complete los campos entre corchetes con los "
        "datos del responsable antes de enviar."
    )
    return "\n".join(lineas)
