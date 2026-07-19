"""Agente Anti-Suplantación — sección 3.2 del plan (caso de validación anonimizado).

Envuelve dnstwist, Sherlock, y documenta certthreat / phishing_catcher
(CertStream) y Google Safe Browsing. Capa del pipeline: "Agente
Anti-Suplantación" (sección 4) — vigila dominios clon, perfiles falsos y
URLs maliciosas usando el nombre/marca del cliente.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ._shared import ToolNotInstalledError, ToolResult, require_binary, run_command


@dataclass
class DomainVariant:
    fuzzer: str
    domain: str
    dns_a: list[str] = field(default_factory=list)
    registered: bool = False
    raw: dict = field(default_factory=dict)


@dataclass
class AntiSuplantacionResult:
    target: str
    tool: str
    findings: list = field(default_factory=list)
    raw: ToolResult | None = None


def run_dnstwist(domain: str, registered_only: bool = True, timeout: int = 300) -> AntiSuplantacionResult:
    """Genera y verifica permutaciones de un dominio (typosquatting, homográficos).

    Repo: elceef/dnstwist. Capa: Agente Anti-Suplantación, núcleo (sección
    3.2). Detecta variaciones del dominio del cliente que ya están
    registradas y activas — el caso central de validación de este módulo.

    Nota: existe también `mcp-dnstwist` (BurtTheCoder/mcp-dnstwist), un
    servidor MCP que envuelve dnstwist y se puede conectar directo al
    orquestador LangGraph sin pasar por este wrapper de subprocess, si se
    prefiere esa vía de integración.

    Args:
        domain: dominio propio del cliente a proteger (ej. "miempresa.com").
        registered_only: si True, solo devuelve variantes ya registradas
            (equivalente a `-r`).

    Instalación si falta:
        pip install dnstwist

    Raises:
        ToolNotInstalledError: si el binario `dnstwist` no está disponible.
    """
    binary = require_binary("dnstwist", "pip install dnstwist")
    cmd = [binary, "--format", "json", domain]
    if registered_only:
        cmd.insert(1, "-r")
    result = run_command("dnstwist", cmd, timeout=timeout)
    variants: list[DomainVariant] = []
    try:
        data = json.loads(result.stdout) if result.stdout.strip() else []
        for item in data:
            variants.append(
                DomainVariant(
                    fuzzer=item.get("fuzzer", ""),
                    domain=item.get("domain", ""),
                    dns_a=item.get("dns_a", []) or [],
                    registered=bool(item.get("dns_a") or item.get("dns_ns")),
                    raw=item,
                )
            )
    except json.JSONDecodeError:
        pass
    result.parsed = variants
    return AntiSuplantacionResult(target=domain, tool="dnstwist", findings=variants, raw=result)


def generate_domain_variants(domain: str) -> set[str]:
    """Genera el conjunto de permutaciones typosquatting/homográficas de `domain`.

    Motor: `dnstwist.Fuzzer`, la MISMA librería que usa `run_dnstwist()` de
    arriba — pero llamada directamente en proceso (import de la librería,
    no `subprocess`) y SIN resolución DNS. `run_dnstwist()` dispara el
    binario `dnstwist` vía `run_command` porque, dentro del pipeline bajo
    demanda, sí queremos que resuelva DNS y confirme qué variantes están
    realmente registradas (`registered_only=True`) — eso tarda segundos
    por dominio.

    Este helper existe para el caso de uso opuesto: el listener de
    CertStream (`api/certstream_listener.py`, Item 5 del backlog) necesita
    comparar decenas de dominios por segundo contra el conjunto de
    variantes de cada activo de cada tenant, y no puede permitirse ni un
    subprocess ni una resolución DNS por comparación — CertStream ya nos
    dice que el dominio existe (se le acaba de emitir un certificado), así
    que la única pregunta es "¿es una permutación plausible de alguno de
    mis dominios vigilados?", que es pura comparación de cadenas en
    memoria. Reutiliza el mismo motor de fuzzing que ya usa el resto del
    módulo en vez de reimplementar reglas de typosquatting.

    Excluye el propio `domain` (dnstwist lo incluye con `fuzzer='*original'`).

    Raises:
        Nada relacionado con herramientas externas — `dnstwist` es una
        librería Python normal (`import dnstwist`), no un binario aparte.
        Si el paquete no está instalado, esto lanza `ImportError` igual
        que cualquier import faltante; el llamador decide si eso es fatal.
    """
    import dnstwist

    fuzzer = dnstwist.Fuzzer(domain)
    fuzzer.generate()
    return {
        variante["domain"]
        for variante in fuzzer.domains
        if variante.get("fuzzer") != "*original"
    }


def registrable_domain(hostname: str) -> str:
    """Devuelve el dominio registrable (apex) de `hostname`, ej. 'www.foo.co.uk' -> 'foo.co.uk'.

    Envuelve `dnstwist.domain_tld`, que usa una lista de sufijos públicos
    real (maneja 'co.uk', 'com.co', etc.) en vez de un `split('.')` ingenuo
    — importante para Colombia (`.com.co`) y para no confundir subdominios
    con el dominio base al hacer matching contra dominios de CertStream,
    que casi siempre incluyen subdominios (`www.`, `mail.`, wildcards).
    """
    import dnstwist

    _sub, dominio, tld = dnstwist.domain_tld(hostname)
    if not dominio:
        return hostname
    return f"{dominio}.{tld}" if tld else dominio


def run_sherlock(username: str, timeout: int = 180) -> AntiSuplantacionResult:
    """Busca un nombre de usuario/marca en +400 redes sociales con Sherlock.

    Repo: sherlock-project/sherlock. Capa: Agente Anti-Suplantación, redes
    sociales (sección 3.2). Sirve para detectar perfiles falsos que se
    hacen pasar por el negocio (caso de validación: suplantación en
    WhatsApp/Instagram).

    Instalación si falta:
        pip install sherlock-project

    Raises:
        ToolNotInstalledError: si el binario `sherlock` no está disponible.
    """
    binary = require_binary("sherlock", "pip install sherlock-project")
    cmd = [binary, username, "--print-found", "--no-color", "--timeout", "30"]
    result = run_command("sherlock", cmd, timeout=timeout)
    found_urls: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("http://") or line.startswith("https://"):
            found_urls.append(line)
        elif ": http" in line:
            # formato típico: "[+] SiteName: https://site.com/user"
            found_urls.append(line.split(": ", 1)[-1].strip())
    result.parsed = found_urls
    return AntiSuplantacionResult(target=username, tool="sherlock", findings=found_urls, raw=result)


def check_certthreat(domain: str) -> AntiSuplantacionResult:
    """Marcador de integración para certthreat (monitoreo de marca vía CT logs).

    Repo: PAST2212/certthreat. Capa: Agente Anti-Suplantación (sección
    3.2) — variante enfocada en monitorear nombres de marca y dominios de
    correo, más cercana a un caso de negocio real que al genérico
    phishing_catcher.

    Esta herramienta no es un paquete pip/go instalable de un comando —
    es un script que se clona y configura con las palabras clave de marca
    a vigilar. No se clona automáticamente aquí porque requiere
    configuración específica del cliente (lista de keywords, dominios de
    correo propios). Para integrarlo:

        git clone https://github.com/PAST2212/certthreat tools/vendor/certthreat
        # luego configurar keywords/dominios según su README y correr
        # su script principal (consume el stream de Certificate Transparency).

    Raises:
        ToolNotInstalledError: siempre, hasta que se clone y configure
            manualmente (ver instrucciones arriba).
    """
    raise ToolNotInstalledError(
        "certthreat",
        "git clone https://github.com/PAST2212/certthreat tools/vendor/certthreat "
        "y configurar keywords de marca según su README (no es un binario "
        "de instalación automática de un solo comando)",
    )


def check_phishing_catcher(domain_keywords: list[str] | None = None) -> AntiSuplantacionResult:
    """Marcador de integración para phishing_catcher (CertStream en tiempo real).

    Repo: x0rz/phishing_catcher. Capa: Agente Anti-Suplantación, monitoreo
    continuo (sección 3.2). Escucha en vivo los logs de Certificate
    Transparency y marca dominios sospechosos apenas se emite su
    certificado SSL — antes de que el sitio esté activo.

    Es un proceso de larga duración (escucha un websocket permanentemente),
    no una llamada puntual de subprocess como las demás funciones de este
    módulo — por eso no se ejecuta aquí vía `run_command`. Para integrarlo
    al pipeline se recomienda correrlo como un servicio/worker aparte que
    escriba alertas a una cola o base de datos que el orquestador consulte.

    Instalación:
        pip install certstream tqdm
        git clone https://github.com/x0rz/phishing_catcher tools/vendor/phishing_catcher
        python tools/vendor/phishing_catcher/certstream_catcher.py

    Raises:
        ToolNotInstalledError: siempre — este wrapper es solo el marcador
            de documentación; el proceso real se corre por separado.
    """
    raise ToolNotInstalledError(
        "phishing_catcher",
        "pip install certstream tqdm && git clone "
        "https://github.com/x0rz/phishing_catcher tools/vendor/phishing_catcher "
        "(se corre como proceso/worker de larga duración, no como llamada puntual)",
    )


def check_safe_browsing(url: str, api_key: str | None = None) -> AntiSuplantacionResult:
    """Verifica si una URL está marcada como maliciosa en Google Safe Browsing.

    Servicio: Google Safe Browsing API (gratuita, requiere API key propia).
    Capa: Agente de Verificación (anti-suplantación), sección 3.2.

    Args:
        url: URL sospechosa a verificar (ej. un dominio clon detectado
            por dnstwist).
        api_key: API key de Google Safe Browsing. Si no se pasa, se debe
            configurar la variable de entorno GOOGLE_SAFE_BROWSING_API_KEY.

    Raises:
        ToolNotInstalledError: si no hay API key configurada, o si falta
            el paquete `requests`.
    """
    import os

    key = api_key or os.environ.get("GOOGLE_SAFE_BROWSING_API_KEY")
    if not key:
        raise ToolNotInstalledError(
            "google-safe-browsing-api-key",
            "obtener una API key gratuita en "
            "https://developers.google.com/safe-browsing/v4/get-started "
            "y exportarla como GOOGLE_SAFE_BROWSING_API_KEY",
        )
    try:
        import requests
    except ImportError as exc:
        raise ToolNotInstalledError("requests", "pip install requests") from exc

    endpoint = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={key}"
    payload = {
        "client": {"clientId": "ciberseguridad-agente", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE"],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url}],
        },
    }
    resp = requests.post(endpoint, json=payload, timeout=30)
    data = resp.json() if resp.content else {}
    matches = data.get("matches", [])
    raw = ToolResult(
        tool="safe-browsing-api",
        command=["POST", endpoint.split("?")[0]],
        returncode=0 if resp.ok else resp.status_code,
        stdout=resp.text,
        stderr="" if resp.ok else f"HTTP {resp.status_code}",
        parsed=matches,
    )
    return AntiSuplantacionResult(target=url, tool="safe-browsing-api", findings=matches, raw=raw)
