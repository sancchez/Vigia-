"""Agente Anti-Suplantación — sección 3.2 del plan (caso de validación anonimizado).

Envuelve dnstwist, Sherlock, y documenta certthreat / phishing_catcher
(CertStream) y Google Safe Browsing. Capa del pipeline: "Agente
Anti-Suplantación" (sección 4) — vigila dominios clon, perfiles falsos y
URLs maliciosas usando el nombre/marca del cliente.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

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


# ---------------------------------------------------------------------------
# Fase 3a (openspec/changes/fase3a-antisuplantacion-y-scoring): scoring
# transparente 0-4 de infraestructura activa por dominio lookalike, y
# screenshot best-effort como evidencia de contenido clonado -- ver
# design.md, sección "Open-core routing" (esto es PÚBLICO: una cuenta plana
# de 4 señales booleanas, sin pesos ni umbrales calibrados).
# ---------------------------------------------------------------------------


@dataclass
class InfraScore:
    """Puntaje transparente 0-4 de infraestructura activa de un dominio lookalike.

    `score = sum([dns_a, mx, ssl, http])` -- cualquiera puede reimplementar
    esta cuenta leyendo esta línea (ver design.md, tabla "Open-core
    routing"). Una señal que no responde, o cuya herramienta subyacente no
    está instalada (ej. `dnspython` para `mx`), cuenta como `False`, nunca
    rompe el cálculo de las demás señales (spec.md, escenario "Probe MX no
    disponible degrada, no rompe").
    """

    dns_a: bool = False
    mx: bool = False
    ssl: bool = False
    http: bool = False
    ssl_not_before: str | None = None
    """ISO 8601 UTC del `NotBefore` del certificado SSL del clon, si el
    handshake tuvo éxito -- insumo del cálculo de MTTD (best-effort) en
    `api/main.py::obtener_kpis_antisuplantacion`. `None` si `ssl=False` o si
    el certificado no trae esa fecha."""
    score: int = 0


def _probe_dns_a(dominio: str, timeout: int) -> bool:
    """True si `dominio` resuelve al menos un registro A (o AAAA vía el
    mismo `socket.gethostbyname`, que sigue el resolver del sistema)."""
    try:
        socket.setdefaulttimeout(timeout)
        socket.gethostbyname(dominio)
        return True
    except (socket.gaierror, socket.timeout, OSError):
        return False
    finally:
        socket.setdefaulttimeout(None)


def _probe_mx(dominio: str, timeout: int) -> bool:
    """True si `dominio` tiene al menos un registro MX -- degrada a `False`
    sin romper si `dnspython` no está instalado (mismo espíritu que
    `tools/asset_verification.py::verificar_dns_txt`, que sí depende de
    `dnspython` como requisito duro; acá es opcional porque el resto del
    scoring debe seguir funcionando sin él)."""
    try:
        import dns.resolver
    except ImportError:
        return False
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        respuestas = resolver.resolve(dominio, "MX")
        return len(respuestas) > 0
    except Exception:  # noqa: BLE001 -- NXDOMAIN/NoAnswer/Timeout/lo que sea: "sin MX", no un 500
        return False


def _probe_ssl(dominio: str, timeout: int) -> tuple[bool, str | None]:
    """Handshake SSL real a `dominio:443`; si tiene éxito, extrae `NotBefore`.

    `verify_mode=CERT_NONE` a propósito: un dominio clon típicamente usa un
    certificado de un CA gratuito válido igual, pero lo que importa aquí es
    "¿responde SSL en absoluto?" (evidencia de infraestructura activa), no
    validar la cadena de confianza -- un cert autofirmado o de un CA no
    reconocido igual cuenta como "SSL vivo" para este propósito.
    """
    try:
        contexto = ssl.create_default_context()
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE
        with socket.create_connection((dominio, 443), timeout=timeout) as sock:
            with contexto.wrap_socket(sock, server_hostname=dominio) as tls:
                cert = tls.getpeercert()
        not_before = cert.get("notBefore") if cert else None
        not_before_iso = None
        if not_before:
            try:
                segundos = ssl.cert_time_to_seconds(not_before)
                not_before_iso = datetime.fromtimestamp(segundos, tz=timezone.utc).isoformat()
            except (ValueError, OverflowError, OSError):
                not_before_iso = None
        return True, not_before_iso
    except Exception:  # noqa: BLE001 -- timeout/refused/TLS error: "SSL no vivo", no un 500
        return False, None


def _probe_http(dominio: str, timeout: int) -> bool:
    """True si el dominio responde a un GET HTTP(S) -- intenta HTTPS primero,
    luego HTTP plano (mismo patrón de fallback que
    `tools/asset_verification.py::verificar_http_well_known`). Cualquier
    respuesta HTTP real (incluido un código de error como 404/500) cuenta
    como "servidor vivo" -- lo que importa es que algo responda en ese
    puerto, no el código de estado."""
    for esquema in ("https", "http"):
        url = f"{esquema}://{dominio}"
        try:
            peticion = urllib.request.Request(url, headers={"User-Agent": "Vigia-BrandProtection/1.0"})
            urllib.request.urlopen(peticion, timeout=timeout)  # noqa: S310 -- URL del dominio clon detectado, GET pasivo
            return True
        except urllib.error.HTTPError:
            return True  # respondió con un código de error -- igual es un servidor vivo
        except Exception:  # noqa: BLE001 -- fallo de red esperable, se intenta el otro esquema
            continue
    return False


def puntuar_infra_lookalike(dominio: str, timeout: int = 10) -> InfraScore:
    """Cuenta cuántas de {DNS-A, MX, SSL, HTTP} responden para `dominio` -- ver `InfraScore`.

    Cada probe es independiente y nunca lanza (todas degradan a `False` con
    gracia): un dominio parqueado sin ningún servicio real da `score=0`, un
    dominio con infraestructura activa completa da `score=4`. Ver spec.md,
    Domain "Scoring de infraestructura activa".
    """
    dns_a = _probe_dns_a(dominio, timeout)
    mx = _probe_mx(dominio, timeout)
    ssl_ok, ssl_not_before = _probe_ssl(dominio, timeout)
    http_ok = _probe_http(dominio, timeout)
    return InfraScore(
        dns_a=dns_a,
        mx=mx,
        ssl=ssl_ok,
        http=http_ok,
        ssl_not_before=ssl_not_before,
        score=sum([dns_a, mx, ssl_ok, http_ok]),
    )


@dataclass
class TriajeLookalikes:
    """Resultado de separar registros defensivos del cliente de amenazas reales.

    `propios` son variantes que el propio dueño de la marca registró para
    protegerse (práctica estándar: un banco compra `banc0lombia.com` para que
    no la compre un estafador). Reportárselas como amenazas es un falso
    positivo grave a nivel comercial -- se le está alarmando al cliente con su
    propia defensa, delante de gente que conoce su infraestructura.
    """

    propios: list[DomainVariant] = field(default_factory=list)
    terceros: list[DomainVariant] = field(default_factory=list)
    con_correo: list[DomainVariant] = field(default_factory=list)
    ns_legitimos: list[str] = field(default_factory=list)


def _ns_de(variante: DomainVariant) -> set[str]:
    return {n for n in (variante.raw or {}).get("dns_ns", []) if n}


def _mx_de(variante: DomainVariant) -> list[str]:
    """MX no vacíos. dnstwist emite `['']` para un MX presente pero sin valor
    resoluble; esa cadena vacía NO cuenta como servidor de correo real."""
    return [m for m in (variante.raw or {}).get("dns_mx", []) if m and m.strip()]


def triar_lookalikes(resultado: AntiSuplantacionResult) -> TriajeLookalikes:
    """Separa registros defensivos del dueño de la marca de amenazas de terceros.

    **Por qué existe (medido 2026-07-25):** una corrida real de `run_dnstwist`
    contra un banco colombiano devolvió 45 dominios lookalike registrados. De
    esos, **10 los había registrado el propio banco** para protegerse -- se
    detectan porque delegan a los MISMOS nameservers que el dominio legítimo.
    Sin este triaje el informe diría "45 amenazas" cuando 35 lo son.

    Criterio de atribución: compartir nameserver autoritativo con el dominio
    legítimo. Es la señal más fuerte disponible sin consultar WHOIS (que está
    mayormente redactado desde GDPR) -- si un dominio delega a la misma
    infraestructura DNS que la marca, lo controla la marca.

    Deliberadamente NO se usa "misma IP" como señal de propiedad: dos dominios
    de estafadores distintos comparten IP todo el tiempo por estar en el mismo
    hosting barato o en el mismo parking, y eso produciría atribuciones falsas.

    `con_correo` son los de terceros que además tienen MX real: pueden ENVIAR
    correo que parezca del cliente. Es el subconjunto urgente.
    """
    original = next(
        (v for v in resultado.findings if v.fuzzer == "*original"),
        None,
    )
    ns_legitimos = _ns_de(original) if original else set()

    triaje = TriajeLookalikes(ns_legitimos=sorted(ns_legitimos))
    for variante in resultado.findings:
        if variante.fuzzer == "*original":
            continue
        # Sin NS legítimos conocidos no se puede atribuir propiedad: se trata
        # todo como de terceros (falso positivo es preferible a pasar por alto
        # una amenaza real, PERO sin inventar una atribución que no se midió).
        if ns_legitimos and (_ns_de(variante) & ns_legitimos):
            triaje.propios.append(variante)
            continue
        triaje.terceros.append(variante)
        if _mx_de(variante):
            triaje.con_correo.append(variante)
    return triaje


def capturar_screenshot(url: str, out_dir: str, timeout: int = 20) -> str | None:
    """Captura un screenshot best-effort de `url` con Playwright/Chromium headless.

    Devuelve la ruta del PNG generado, o `None` si Playwright no está
    instalado (`ImportError`) o si la captura falla por cualquier razón
    (timeout, DNS, TLS, navegador no descargado con
    `playwright install chromium`) -- mismo patrón de degradación con
    gracia que `check_certthreat`/`check_phishing_catcher`: la feature
    (score + KPIs) funciona igual sin este enriquecimiento (spec.md,
    escenario "Screenshot omitido cuando el navegador no está instalado").
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        os.makedirs(out_dir, exist_ok=True)
        ruta = os.path.join(out_dir, f"{uuid.uuid4().hex}.png")
        objetivo = url if "://" in url else f"http://{url}"
        with sync_playwright() as p:
            navegador = p.chromium.launch(headless=True)
            try:
                pagina = navegador.new_page()
                pagina.goto(objetivo, timeout=timeout * 1000)
                pagina.screenshot(path=ruta)
            finally:
                navegador.close()
        return ruta
    except Exception:  # noqa: BLE001 -- best-effort: cualquier fallo de captura es "sin screenshot", no un 500
        return None
