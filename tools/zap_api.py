"""Driver de la API HTTP real de OWASP ZAP -- reemplaza el `subprocess.run(...,
timeout=...)` monolítico de `zap-full-scan.py`/`zap-baseline.py` para el
escaneo ACTIVO (`POST /scan/activo`) por un daemon de ZAP + polling
incremental.

## Por qué existe este módulo (ver HANDOFF.md Item 2 y eval/live_run_report.md)

Antes: `tools.scan.run_zap_active_scan()` invoca el script de conveniencia
`zap-full-scan.py` dentro de un único `docker run` que bloquea el hilo de
Python entero hasta que el script completo (spider + AJAX spider + escaneo
activo + generación de reporte) termina o expira -- en la práctica, 20-35
minutos de bloqueo real observados en Corridas 4 y 12, sin ninguna
visibilidad intermedia de progreso ni forma de "revisar" el estado desde
fuera salvo esperar el timeout completo.

Ahora: este módulo levanta ZAP en modo daemon (`zap.sh -daemon`, la misma
imagen Docker, sin usar el script de conveniencia) y conduce el spider/AJAX
spider/escaneo activo a través de la API HTTP real de ZAP -- arrancar cada
fase es una llamada HTTP que responde de inmediato con un id de scan, y el
progreso se consulta con llamadas HTTP cortas y repetidas
(`.../view/status/`) en vez de un único `subprocess.run` con un timeout
gigante. Esto es lo que pidió el usuario explícitamente: "varias sesiones
cortas en vez de una larga" -- el llamador (`api/main.py`) puede (y debe)
persistir el progreso después de cada poll, así una corrida larga real deja
rastro incremental en la fila `scans` en vez de "corriendo" durante 30
minutos y después un único salto a `completado`/`error`.

## Verificado en vivo contra Juice Shop real (no solo diseñado)

Esta sesión levantó Juice Shop + un contenedor ZAP en modo daemon reales
(imágenes ya existentes localmente) y confirmó, con llamadas HTTP reales:

- El daemon tarda ~45-100s en quedar listo la primera vez que instala
  add-ons (mismo comportamiento que ya paga `zap-full-scan.py`, no es un
  costo nuevo de este enfoque).
- Truco real necesario y no documentado en ningún lado obvio: ZAP identifica
  si una request HTTP entrante es "para su propia API" comparando el header
  `Host` contra su propio puerto interno (el que se le pasó a `-port`, en
  este módulo siempre 8080 dentro del contenedor) -- si se llega a través
  del puerto mapeado del host (ej. `localhost:48290` -> contenedor:8080),
  el `Host` real que manda el cliente HTTP no matchea y ZAP trata la
  petición como si fuera un proxy normal a reenviar, fallando con un 502
  "Connection refused" contra su propio puerto externo. La solución real
  (confirmada en vivo) es forzar el header `Host: localhost:8080` en cada
  request a la API sin importar qué puerto del host esté mapeado -- ver
  `_HOST_HEADER` y `_zap_get` más abajo.
- Spider clásico (`/JSON/spider/...`) sobre Juice Shop real: progreso
  incremental real observado (57% -> 76% -> 56% -> ... -> 100%; NO es
  monotónico -- el denominador crece según se descubren más URLs, así que
  el consumidor de este módulo debe esperar a `status == 100`, no asumir
  que solo sube).
- Escaneo activo (`/JSON/ascan/...`) real contra Juice Shop: 0% -> 2% -> 4%
  -> 11% -> 12% en ~32s de polls cada 4s -- confirma ataque real en curso,
  observable en checkpoints cortos.
- **AJAX Spider real (`/JSON/ajaxSpider/...`, el modo -j que causaba los
  cuelgues originales de 20-35 min):** confirmado con evidencia de proceso
  real dentro del contenedor (`geckodriver` + `firefox-esr --headless`
  activos, `com.crawljax` en los logs de ZAP) -- el mismo patrón de
  Firefox headless real que ya había diagnosticado Corrida 12 vía
  `docker top`, ahora con la ventaja de que el progreso (`numberOfResults`)
  se puede consultar en cualquier momento sin esperar a que termine.
  **Nota real:** el namespace correcto de la API es `ajaxSpider` (no
  `spiderAjax`, que devuelve `no_implementor` -- un error real que costó
  tiempo diagnosticar en esta sesión, documentado aquí para que no se
  repita la confusión).
- Reglas de replacer (`/JSON/replacer/action/addRule/`) para inyectar
  `Authorization: Bearer <token>` -- confirmado con `Result: OK`, mismo
  mecanismo que ya usaba `-config replacer.*` en la versión basada en
  script, ahora vía API directa.

## Qué NO resuelve este módulo (limitación real, no un detalle menor)

Esto es "checkpointed" en el sentido de que el progreso se puede consultar
en llamadas cortas y repetidas, y de que el llamador puede persistir ese
progreso en la DB después de cada poll -- pero el estado de la sesión de
scan en sí (contenedor Docker + ids de scan de ZAP) vive en el hilo de
Python que lo maneja, no en la base de datos. Si el proceso de la API de
Vigia se cae a mitad de un escaneo, el contenedor ZAP queda huérfano (se
limpia con las mismas utilidades de `tools._shared` que usa
`tools.scan._run_zap_script`) pero el escaneo en sí no se puede "retomar"
desde cero tras un reinicio del proceso -- sería necesario persistir
`container_name`/`api_base_url`/`scan_id de ZAP` en la fila `scans` y un
job de reconciliación al arrancar, que es una extensión razonable pero
deliberadamente fuera de alcance de esta sesión (ver HANDOFF.md).
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ._shared import (
    ToolExecutionError,
    docker_force_remove_container,
    require_binary,
    run_command,
)

ZAP_IMAGE = "ghcr.io/zaproxy/zaproxy:stable"
_INTERNAL_PORT = 8080
# Ver docstring del módulo: ZAP solo se reconoce a sí mismo si el Host
# header matchea el puerto con el que arrancó -- no el puerto mapeado del
# host Docker. Se fuerza este header en cada llamada a la API.
_HOST_HEADER = f"localhost:{_INTERNAL_PORT}"


class ZapDaemonError(ToolExecutionError):
    """El daemon de ZAP no llegó a estar listo, o la API respondió con un error real."""


@dataclass
class ZapProgress:
    """Snapshot de progreso de una fase -- lo que se persiste en cada checkpoint."""

    fase: str  # 'iniciando' | 'spider' | 'ajax_spider' | 'ascan' | 'completado' | 'error'
    detalle: str
    porcentaje: Optional[int] = None


@dataclass
class ZapCheckpointedResult:
    target: str
    findings: list[dict] = field(default_factory=list)
    fases_completadas: list[str] = field(default_factory=list)
    detalle_final: str = ""


def _free_host_port() -> int:
    """Puerto libre efímero del host para mapear el 8080 del contenedor.

    Necesario para que escaneos concurrentes (dos scan_id distintos al
    mismo tiempo) no choquen por el mismo puerto -- a diferencia del
    contenedor daemon único que se probó manualmente en esta sesión.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _zap_get(
    base_url: str, path: str, params: dict | None = None, timeout: int = 25, retries: int = 2
) -> dict:
    """GET contra la API de ZAP con reintentos cortos para lecturas transitorias.

    **Hallazgo real de esta sesión, no hipotético:** una corrida en vivo
    contra Juice Shop real (con varios contenedores Docker de otros
    procesos corriendo en paralelo en la misma máquina -- la misma
    contención de recursos que ya había diagnosticado Corrida 12) produjo
    un `ReadTimeout` real durante el AJAX Spider (la fase que levanta
    Firefox headless dentro del contenedor daemon, la más pesada de las
    tres). Antes de este reintento, un solo poll lento tumbaba el escaneo
    ACTIVO completo con `estado='error'` incluso cuando el daemon seguía
    perfectamente vivo -- exactamente el tipo de fragilidad que el diseño
    "checkpointed" de este módulo debería evitar (un poll individual lento
    no debería costar todo el progreso ya alcanzado). Un par de reintentos
    cortos con backoff absorbe ese caso real sin ocultar un daemon
    genuinamente caído (que sigue fallando tras los reintentos y se
    propaga igual).
    """
    import requests

    ultimo_error: Exception | None = None
    for intento in range(retries + 1):
        try:
            resp = requests.get(
                f"{base_url}{path}",
                params=params or {},
                headers={"Host": _HOST_HEADER},
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            ultimo_error = exc
            if intento < retries:
                time.sleep(2 * (intento + 1))
    assert ultimo_error is not None
    raise ultimo_error


def start_daemon(container_name: str, host_port: int | None = None) -> tuple[str, int]:
    """Arranca ZAP en modo daemon vía Docker y devuelve `(base_url, host_port)`.

    Raises:
        ToolNotInstalledError: si `docker` no está en PATH.
        ZapDaemonError: si el `docker run -d` falla en sí (no cubre "el
            daemon tardó en responder" -- eso lo maneja `wait_ready`).
    """
    binary = require_binary(
        "docker",
        "instalar Docker Desktop (https://docs.docker.com/desktop/) y luego: "
        "docker pull ghcr.io/zaproxy/zaproxy:stable",
    )
    port = host_port or _free_host_port()
    cmd = [
        binary,
        "run",
        "-d",
        "--name",
        container_name,
        "--add-host",
        "host.docker.internal:host-gateway",
        "-p",
        f"{port}:{_INTERNAL_PORT}",
        ZAP_IMAGE,
        "zap.sh",
        "-daemon",
        "-host",
        "0.0.0.0",
        "-port",
        str(_INTERNAL_PORT),
        "-config",
        "api.disablekey=true",
        "-config",
        "api.addrs.addr.name=.*",
        "-config",
        "api.addrs.addr.regex=true",
    ]
    result = run_command("zap-daemon-start", cmd, timeout=30)
    if not result.ok:
        raise ZapDaemonError(
            f"No se pudo arrancar el contenedor daemon de ZAP: {result.stderr or result.stdout}"
        )
    return f"http://localhost:{port}", port


def wait_ready(base_url: str, timeout: int = 240, poll_interval: float = 3.0) -> None:
    """Bloquea (poco, y en pasos cortos) hasta que la API de ZAP responde.

    Cada contenedor daemon arranca "en frío" -- la imagen no trae los
    add-ons (spiderAjax, selenium, webdriverlinux, retire, ...)
    preinstalados en el filesystem del contenedor, así que se reinstalan
    en cada escaneo nuevo (mismo costo que ya pagaba `zap-full-scan.py`
    con el enfoque anterior, no es un costo nuevo de este módulo). En
    esta sesión, en un host tranquilo tardó ~47-57s; bajo contención real
    de Docker (varios contenedores de otros procesos corriendo en
    paralelo -- el mismo tipo de contención que ya había diagnosticado
    Corrida 12 para el enfoque anterior) se observó una corrida real que
    superó los 120s sin todavía estar lista, con el propio contenedor
    confirmado sano y progresando (`docker logs` mostrando instalación de
    add-ons en curso, no colgado) -- de ahí el timeout generoso por
    defecto. `poll_interval` corto para no perder tiempo una vez que ya
    está listo.

    **Optimización real no implementada esta sesión (ver HANDOFF.md):**
    montar un volumen persistente en `/home/zap/.ZAP` reutilizable entre
    escaneos eliminaría este costo de reinstalación repetida casi por
    completo a partir del segundo escaneo -- se dejó fuera de esta sesión
    por el riesgo de compartir estado entre escaneos concurrentes de
    tenants distintos sin diseñarlo con cuidado (un volumen compartido
    ingenuo podría filtrar contexto/sesión de un escaneo a otro).
    """
    deadline = time.monotonic() + timeout
    ultimo_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _zap_get(base_url, "/JSON/core/view/version/", timeout=5)
            return
        except Exception as exc:  # noqa: BLE001 -- red/arranque, cualquier excepción implica "todavía no"
            ultimo_error = exc
            time.sleep(poll_interval)
    raise ZapDaemonError(
        f"El daemon de ZAP en {base_url} no respondió dentro de {timeout}s. "
        f"Último error: {ultimo_error}"
    )


def add_bearer_auth_replacer(base_url: str, bearer_token: str) -> None:
    """Inyecta `Authorization: Bearer <token>` en todas las peticiones salientes de ZAP.

    Equivalente vía API real a lo que `tools.scan.run_zap_active_scan`
    lograba con `-config replacer.*` al invocar el script de conveniencia.
    """
    _zap_get(
        base_url,
        "/JSON/replacer/action/addRule/",
        params={
            "description": "vigia-auth",
            "enabled": "true",
            "matchType": "REQ_HEADER",
            "matchString": "Authorization",
            "matchRegex": "false",
            "replacement": f"Bearer {bearer_token}",
        },
    )


def spider_start(base_url: str, target_url: str) -> str:
    data = _zap_get(base_url, "/JSON/spider/action/scan/", params={"url": target_url, "recurse": "true"})
    return str(data["scan"])


def spider_status(base_url: str, scan_id: str) -> int:
    data = _zap_get(base_url, "/JSON/spider/view/status/", params={"scanId": scan_id})
    return int(data["status"])


def ajax_spider_start(base_url: str, target_url: str) -> None:
    _zap_get(base_url, "/JSON/ajaxSpider/action/scan/", params={"url": target_url})


def ajax_spider_status(base_url: str) -> str:
    """'running' o 'stopped' -- el AJAX Spider no reporta porcentaje, solo estado + conteo."""
    data = _zap_get(base_url, "/JSON/ajaxSpider/view/status/")
    return str(data["status"])


def ajax_spider_results_count(base_url: str) -> int:
    data = _zap_get(base_url, "/JSON/ajaxSpider/view/numberOfResults/")
    return int(data["numberOfResults"])


def ajax_spider_stop(base_url: str) -> None:
    try:
        _zap_get(base_url, "/JSON/ajaxSpider/action/stop/")
    except Exception:  # noqa: BLE001 -- best-effort, puede que ya haya terminado solo
        pass


def ascan_start(base_url: str, target_url: str) -> str:
    data = _zap_get(base_url, "/JSON/ascan/action/scan/", params={"url": target_url, "recurse": "true"})
    return str(data["scan"])


def ascan_status(base_url: str, scan_id: str) -> int:
    data = _zap_get(base_url, "/JSON/ascan/view/status/", params={"scanId": scan_id})
    return int(data["status"])


def ascan_stop(base_url: str, scan_id: str) -> None:
    try:
        _zap_get(base_url, "/JSON/ascan/action/stop/", params={"scanId": scan_id})
    except Exception:  # noqa: BLE001 -- best-effort
        pass


def get_alerts(base_url: str, target_url: str) -> list[dict]:
    data = _zap_get(base_url, "/JSON/core/view/alerts/", params={"baseurl": target_url})
    return data.get("alerts", []) or []


def stop_daemon(container_name: str) -> None:
    """Detiene y elimina el contenedor daemon -- mismo helper best-effort que usa
    `tools.scan._run_zap_script` para no dejar contenedores huérfanos."""
    docker_force_remove_container(require_binary("docker", "instalar Docker Desktop"), container_name)


def run_checkpointed_active_scan(
    target_url: str,
    minutes: int,
    bearer_token: str | None,
    ajax_spider: bool,
    scan_id: str,
    on_progress: Callable[[ZapProgress], None] | None = None,
    poll_interval: float = 4.0,
) -> ZapCheckpointedResult:
    """Corre spider (+ AJAX spider opcional) y escaneo activo vía la API real de ZAP.

    A diferencia de `tools.scan.run_zap_active_scan` (un único `docker run`
    bloqueante con timeout fijo), esta función nunca bloquea más que
    `poll_interval` segundos seguidos -- cada fase es un bucle de llamadas
    HTTP cortas a `.../view/status/`, y `on_progress` se invoca después de
    cada una para que el llamador (ver `api/main.py`) pueda persistir el
    checkpoint en la DB de inmediato. Si el presupuesto de `minutes` se
    agota a mitad de cualquier fase, esa fase se detiene explícitamente
    (`.../action/stop/`) y se recogen los hallazgos que sí se alcanzaron a
    encontrar -- nunca se pierde todo el progreso por exceder el
    presupuesto, a diferencia del timeout monolítico anterior.

    El contenedor daemon SIEMPRE se limpia en un `finally`, tanto en éxito
    como en cualquier excepción -- mismo principio que el fix de Bug 1 en
    `tools.scan._run_zap_script`: nunca dejar un contenedor huérfano.

    Raises:
        ToolNotInstalledError: si `docker` no está en PATH.
        ZapDaemonError: si el daemon nunca llegó a estar listo.
    """

    def _reportar(fase: str, detalle: str, porcentaje: int | None = None) -> None:
        if on_progress is not None:
            on_progress(ZapProgress(fase=fase, detalle=detalle, porcentaje=porcentaje))

    container_name = f"vigia-zap-{scan_id}"
    presupuesto_total = max(60, minutes * 60)
    # OJO: el reloj del presupuesto arranca DESPUÉS de que el daemon esté
    # listo, no desde el inicio de la función. Bug real encontrado en vivo
    # esta sesión: si se cuenta desde el arranque del contenedor, el tiempo
    # de instalación de add-ons (45-100s en un host tranquilo, más de 120s
    # confirmado bajo contención real de Docker -- ver `wait_ready`) le
    # comía presupuesto al escaneo real sin que el usuario pidiera eso --
    # un `minutes=1` podía terminar con 0% de spidering real hecho, y aun
    # así reportar 'completado' con 0 hallazgos de forma engañosa.
    inicio: float | None = None

    def _tiempo_restante() -> float:
        if inicio is None:
            return presupuesto_total
        return presupuesto_total - (time.monotonic() - inicio)

    zap_target = target_url.replace("localhost", "host.docker.internal").replace(
        "127.0.0.1", "host.docker.internal"
    )

    resultado = ZapCheckpointedResult(target=target_url)
    base_url = ""
    try:
        _reportar("iniciando", "Arrancando el daemon de ZAP…")
        base_url, _port = start_daemon(container_name)
        wait_ready(base_url)
        inicio = time.monotonic()
        _reportar("iniciando", "Daemon de ZAP listo.")

        if bearer_token:
            add_bearer_auth_replacer(base_url, bearer_token)

        # --- Fase 1: spider clásico (siempre corre, es rápido y barato) ---
        spider_id = spider_start(base_url, zap_target)
        while _tiempo_restante() > 0:
            pct = spider_status(base_url, spider_id)
            _reportar("spider", f"Spider clásico: {pct}%", pct)
            if pct >= 100:
                break
            time.sleep(poll_interval)
        resultado.fases_completadas.append("spider")

        # --- Fase 2: AJAX spider opcional (el modo que causaba los cuelgues) ---
        if ajax_spider and _tiempo_restante() > 0:
            ajax_spider_start(base_url, zap_target)
            # Presupuesto propio para no dejar sin tiempo al escaneo activo:
            # hasta el 40% del tiempo restante en este punto.
            presupuesto_ajax = _tiempo_restante() * 0.4
            inicio_ajax = time.monotonic()
            while _tiempo_restante() > 0 and (time.monotonic() - inicio_ajax) < presupuesto_ajax:
                estado = ajax_spider_status(base_url)
                n = ajax_spider_results_count(base_url)
                _reportar("ajax_spider", f"AJAX Spider: {estado}, {n} resultado(s)")
                if estado != "running":
                    break
                time.sleep(poll_interval)
            ajax_spider_stop(base_url)
            resultado.fases_completadas.append("ajax_spider")

        # --- Fase 3: escaneo activo (el ataque real) ---
        if _tiempo_restante() > 0:
            ascan_id = ascan_start(base_url, zap_target)
            while _tiempo_restante() > 0:
                pct = ascan_status(base_url, ascan_id)
                _reportar("ascan", f"Escaneo activo: {pct}%", pct)
                if pct >= 100:
                    break
                time.sleep(poll_interval)
            else:
                # Se acabó el presupuesto a mitad del ascan -- detenerlo
                # explícitamente en vez de dejarlo corriendo sin que nadie
                # lo observe, y quedarnos con lo que sí se alcanzó a
                # encontrar hasta este punto.
                ascan_stop(base_url, ascan_id)
                _reportar("ascan", "Presupuesto de tiempo agotado -- escaneo activo detenido a mitad.")
            resultado.fases_completadas.append("ascan")

        resultado.findings = get_alerts(base_url, zap_target)
        resultado.detalle_final = (
            f"Fases completadas: {', '.join(resultado.fases_completadas)}. "
            f"{len(resultado.findings)} hallazgo(s)."
        )
        _reportar("completado", resultado.detalle_final)
        return resultado
    finally:
        stop_daemon(container_name)
