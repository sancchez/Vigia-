"""Verificación de propiedad de dominio para `assets` — cierra un gap real de producción.

Antes de este módulo, `POST /assets` (`api/main.py`) dejaba que cualquier
tenant autenticado registrara CUALQUIER dominio como propio -- sin ninguna
prueba de control -- y ese registro por sí solo bastaba para que
`POST /scan`/`POST /scan/activo` lo trataran como autorizado para escanear.
Un tenant malicioso (o simplemente descuidado) podía registrar
`microsoft.com` como "suyo" y el sistema lo habría escaneado igual que un
dominio legítimamente propio. Esto es tanto un abuso real posible como la
causa raíz de un near-miss real de esta sesión: un agente de prueba casi
confundió "el activo está registrado" con "está autorizado".

## El patrón elegido: el mismo que usan Google Search Console / Detectify

Un token único por asset, y el tenant demuestra control DEL DOMINIO (del
*nombre*, no del contenido ni del servidor) publicando ese token en uno de
dos lugares que solo alguien con control real puede escribir:

1. **DNS TXT** en `_vigia-challenge.<dominio>` (método principal). Requiere
   acceso al panel DNS del dominio -- la señal más fuerte de "controlas el
   dominio", independiente de qué haya alojado detrás (ni siquiera hace
   falta que el dominio resuelva a un sitio web todavía).
2. **Archivo bien-conocido** en
   `https://<dominio>/.well-known/vigia-verification.txt` (alternativa).
   Requiere poder publicar contenido en el servidor que responde por ese
   dominio -- prueba real de control también, y la alternativa estándar
   cuando alguien administra el sitio pero no tiene acceso al DNS (agencias
   web, por ejemplo).

Se implementan AMBOS métodos de verdad (no solo uno, había margen de
tiempo): DNS vía `dnspython` (ya era una dependencia transitiva real de este
proyecto a través de `dnstwist`, ver `tools/antisuplantacion.py` -- ahora se
declara explícita en `pyproject.toml` porque este módulo la importa
directo, no solo a través de ese wrapper), HTTP vía `urllib.request` de la
librería estándar -- cero dependencias nuevas para ese camino.

## La excepción de localhost / IP privada -- por qué es segura, no un atajo

Este proyecto se prueba constantemente contra `localhost`, contenedores
Docker locales (Juice Shop, DVWA) y dominios sintéticos (`miempresatest.com`)
que jamás podrán pasar una verificación DNS/HTTP pública real -- exigirles
verificación real bloquearía todo el flujo de laboratorio del que depende
el resto del proyecto para probarse a sí mismo (ver HANDOFF.md).

La exención NO es "cualquier dominio que el tenant diga que es de prueba".
Es estrictamente: el host/IP declarado, tal cual, es `localhost` o cae
dentro de un rango de red privada/loopback/link-local (RFC 1918, RFC 4193,
127.0.0.0/8, RFC 3927 169.254.0.0/16, y sus equivalentes IPv6, todos vía
`ipaddress.ip_address(...).is_private/is_loopback/is_link_local`, la misma
librería estándar, no una lista de rangos hecha a mano).

Por qué es seguro: un target así SOLO es alcanzable desde la misma
máquina/red que ya corre Vigia -- no existe un tercero real en el mundo cuyo
sistema pueda verse afectado por que un tenant registre "localhost" como su
asset, porque escanear "localhost" desde un proceso dado siempre apunta al
proceso de quien ejecuta el escaneo, nunca al de otra persona. Es lo
opuesto de registrar `microsoft.com`: ahí sí hay un tercero real (Microsoft)
cuyo sistema resultaría escaneado sin su consentimiento si no exigiéramos
prueba de control.

Importante -- la exención se decide sobre el HOST/IP LITERAL que el tenant
escribió en `assets.valor`, nunca sobre una resolución DNS en vivo del
nombre. Si se resolviera el nombre en el momento de decidir la exención, un
atacante podría apuntar temporalmente un dominio público real a una IP
privada (DNS rebinding) para colarse por la excepción y luego repuntar el
DNS a la IP real antes de escanear. Comparar el string literal contra
'localhost'/rangos de IP privada cierra esa puerta: un dominio público de
verdad (ej. 'miempresatest.com') SIEMPRE requiere verificación real sin
importar a qué resuelva hoy -- que es exactamente el caso que esta sesión
documentó como "no puede pasar verificación real" y que, correctamente, NO
se exime.
"""

from __future__ import annotations

import ipaddress
import secrets
import urllib.error
import urllib.request
from urllib.parse import urlparse

DNS_TXT_SUBDOMAIN = "_vigia-challenge"
WELL_KNOWN_PATH = "/.well-known/vigia-verification.txt"
TOKEN_PREFIX = "vigia-verify-"

METODOS_VALIDOS = ("dns_txt", "http_file")


def generar_token() -> str:
    """Token único, impredecible, por asset -- 32 hex chars (128 bits) de `secrets`."""
    return f"{TOKEN_PREFIX}{secrets.token_hex(16)}"


def _hostname_de(valor: str) -> str:
    """Extrae el hostname de una URL o de un 'dominio pelado' sin esquema.

    Misma lógica que `api/main.py::_hostname_de_target` (duplicada a
    propósito -- este módulo no debe importar de `api/main.py`, sería una
    dependencia invertida: `tools/` es una capa por debajo de `api/`).
    """
    candidato = valor if "://" in valor else f"http://{valor}"
    return (urlparse(candidato).hostname or "").strip().lower()


def es_target_exento_de_verificacion(tipo: str, valor: str) -> bool:
    """True si `valor` no requiere verificación de propiedad -- ver docstring del módulo.

    Estrictamente: 'localhost' (o cualquier `*.localhost`, reservado por
    RFC 6761 para el propio host) o una IP que cae en rango
    privado/loopback/link-local, ya sea declarada directo (`tipo == 'ip'`)
    o como el host de un dominio/URL declarado (ej. un asset 'app' cuyo
    valor es 'http://127.0.0.1:3000').
    """
    valor = (valor or "").strip().lower()
    if not valor:
        return False

    if tipo == "ip":
        host = valor
    else:
        host = _hostname_de(valor) or valor
        host = host.split(":")[0]  # 'localhost:3000' -> 'localhost'
        if host == "localhost" or host.endswith(".localhost"):
            return True

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def asset_autorizado_para_escanear(tipo: str, valor: str, verificado: bool) -> bool:
    """La pregunta que de verdad importa para gatear un scan: verificado O exento."""
    return bool(verificado) or es_target_exento_de_verificacion(tipo, valor)


def instrucciones_verificacion(tipo: str, valor: str, token: str) -> dict | None:
    """Arma las instrucciones concretas (DNS TXT + archivo HTTP) para mostrarle al tenant.

    `None` para assets tipo 'ip' (ningún dominio del que colgar un TXT o un
    `.well-known`) o para assets ya exentos (no hace falta que el tenant
    haga nada).
    """
    if tipo == "ip" or es_target_exento_de_verificacion(tipo, valor):
        return None
    dominio = _hostname_de(valor) or valor
    return {
        "dns_txt": {
            "registro": f"{DNS_TXT_SUBDOMAIN}.{dominio}",
            "tipo": "TXT",
            "valor": token,
        },
        "http_file": {
            "url": f"https://{dominio}{WELL_KNOWN_PATH}",
            "contenido": token,
        },
    }


def verificar_dns_txt(dominio: str, token: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Consulta de verdad el registro TXT en `_vigia-challenge.<dominio>`.

    Usa `dnspython` (`dns.resolver`) contra los resolutores DNS del sistema
    -- una consulta DNS real, no simulada. Cualquier fallo esperable
    (dominio sin ese TXT, timeout, NXDOMAIN) se traduce a `(False, detalle)`
    en vez de propagar una excepción -- el mismo espíritu de degradación con
    gracia que `tools/_shared.py::ToolExecutionError`, aunque este módulo no
    ejecuta subprocesos.
    """
    import dns.exception
    import dns.resolver

    nombre = f"{DNS_TXT_SUBDOMAIN}.{dominio}"
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        respuestas = resolver.resolve(nombre, "TXT")
    except dns.resolver.NXDOMAIN:
        return False, f"No existe el registro TXT '{nombre}' todavía -- créalo con el valor del token."
    except dns.resolver.NoAnswer:
        return False, f"'{nombre}' existe pero no tiene registros TXT."
    except dns.exception.Timeout:
        return False, f"Timeout consultando DNS para '{nombre}' (> {timeout}s)."
    except Exception as exc:  # noqa: BLE001 -- cualquier fallo de resolución es "no verificado", no un 500
        return False, f"No se pudo consultar DNS para '{nombre}': {exc}"

    for respuesta in respuestas:
        partes = [s.decode() if isinstance(s, bytes) else s for s in respuesta.strings]
        contenido = "".join(partes)
        if token in contenido:
            return True, f"Token encontrado en el TXT de '{nombre}'."
    return False, f"'{nombre}' tiene registro(s) TXT pero ninguno contiene el token esperado."


def verificar_http_well_known(dominio: str, token: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Descarga de verdad `https://<dominio>/.well-known/vigia-verification.txt` (con fallback a http).

    Sin dependencias nuevas -- `urllib.request` de la librería estándar
    alcanza para un GET simple con timeout. Intenta HTTPS primero (lo
    esperable en producción); si la conexión falla del todo (no solo un
    404/500 HTTP), reintenta por HTTP plano antes de rendirse -- cubre el
    caso real de un dominio de laboratorio sin TLS configurado.
    """
    ultimo_error = f"No se pudo alcanzar ningún esquema para '{dominio}{WELL_KNOWN_PATH}'."
    for esquema in ("https", "http"):
        url = f"{esquema}://{dominio}{WELL_KNOWN_PATH}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 -- URL propia del tenant, no de un tercero
                cuerpo = resp.read(8192).decode("utf-8", errors="replace")
            if token in cuerpo:
                return True, f"Token encontrado en {url}."
            return False, f"{url} respondió pero no contiene el token esperado."
        except urllib.error.HTTPError as exc:
            ultimo_error = f"{url} respondió HTTP {exc.code}."
        except Exception as exc:  # noqa: BLE001 -- fallo de red esperable, no un 500
            ultimo_error = f"No se pudo alcanzar {url}: {exc}"
    return False, ultimo_error


def verificar_asset(tipo: str, valor: str, token: str, metodo: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Dispatcher: ejecuta el método de verificación pedido contra el asset real.

    Lanza `ValueError` para un `tipo`/`metodo` que no debería haber llegado
    aquí (validado ya en la capa HTTP) -- un bug de programación, no un
    resultado esperado de "no verificado".
    """
    if tipo == "ip":
        raise ValueError("La verificación de propiedad no aplica a assets tipo 'ip'.")
    if metodo not in METODOS_VALIDOS:
        raise ValueError(f"método de verificación desconocido: {metodo!r}")

    dominio = _hostname_de(valor) or valor
    if metodo == "dns_txt":
        return verificar_dns_txt(dominio, token, timeout=timeout)
    return verificar_http_well_known(dominio, token, timeout=timeout)
