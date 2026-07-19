"""Listener CertStream — Item 5 del backlog (HANDOFF.md), vigilancia continua real
del módulo anti-suplantación.

Hoy `tools/antisuplantacion.py` (dnstwist + Sherlock) solo corre bajo
demanda dentro del pipeline normal — nada escucha en tiempo real cuando
alguien registra un dominio clon nuevo. CertStream transmite en
near-real-time cada certificado SSL emitido en el mundo (logs de
Certificate Transparency): un dominio lookalike puede aparecer aquí el
mismo día que se registra, a menudo antes de que el sitio de phishing esté
siquiera activo. Es la pieza que de verdad justifica "vigilancia continua"
para anti-suplantación — `api/scheduler.py` solo re-escanea cada N horas,
que no es lo mismo.

Arquitectura (decidida por el usuario, ver HANDOFF.md sección Item 5): el
listener corre como un daemon thread DENTRO del mismo proceso uvicorn,
arrancado desde el lifespan de FastAPI (`api/main.py`) — exactamente el
mismo patrón que `api/scheduler.py` (mismo `get_conn()` por operación,
mismo estilo de start/stop idempotente). NO es un proceso/servicio aparte.

Cómo funciona:
1. Al arrancar (y cada `VIGIA_CERTSTREAM_REFRESH_MINUTES`, default 30),
   construye un mapa {variante_de_dominio: (tenant_id, asset_id, dominio_base)}
   para todos los activos tipo 'dominio' de todos los tenants, usando
   `tools.antisuplantacion.generate_domain_variants()` — el mismo motor
   dnstwist que ya usa el pipeline bajo demanda, llamado en proceso (sin
   subprocess) porque un stream de CT logs global no puede darse el lujo
   de lanzar un subprocess por dominio observado.
2. Se conecta al feed CertStream vía websocket (paquete opcional
   `certstream` — ver guarda de import en `_run_listener`) y por cada
   mensaje `certificate_update` extrae los dominios del certificado recién
   emitido (`data.leaf_cert.all_domains`).
3. Por cada dominio observado, calcula su dominio registrable
   (`tools.antisuplantacion.registrable_domain`) y lo busca en el mapa de
   variantes. Si hay match (y no es el propio dominio del tenant), escribe
   un finding real en la tabla `findings`, enlazado a una fila `scans`
   sintética — el schema exige `scan_id` NOT NULL y aquí no hay un "scan"
   real detrás de un evento de streaming, así que se crea uno de tipo
   'completado' con un solo hallazgo (mismo patrón que
   `api/scheduler.py::run_scan_cycle_once`, que también sintetiza una fila
   `scans` por ciclo).

Degradación (obligatoria — ver `tools/_shared.py::ToolExecutionError`,
mismo espíritu aunque este módulo no ejecuta subprocesos): si el paquete
`certstream` no está instalado, si el feed configurado es inalcanzable, o
si ocurre cualquier otra excepción no prevista, el listener registra el
problema en el log y se apaga solo — nunca debe poder tumbar la API. El
hilo es `daemon=True` y todo el cuerpo de `_run_listener` está envuelto en
manejo de excepciones amplio a propósito, por la misma razón que
`_correr_escaneo_activo_en_background` en `api/main.py` atrapa `Exception`
como último punto de captura posible.

Instalación del paquete opcional:
    pip install certstream

AVISO IMPORTANTE (encontrado al implementar esto, ver
`eval/live_run_report.md` para el detalle completo): el feed público
histórico `wss://certstream.calidog.io` (Cali Dog Security) acepta el
handshake de websocket pero NO transmite ningún mensaje — el servicio fue
descontinuado. A julio 2026 no existe un feed público gratuito de
CertStream mantenido; las alternativas activas (`certstream-server-go`,
`certstream-server-rust`, `go-certstream` de LeakIX) son todas
self-hosted. `VIGIA_CERTSTREAM_URL` es configurable por esto — apuntar a
una instancia propia (Docker) es el paso pendiente para vigilancia
verdaderamente en vivo.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from db.connection import get_conn
from tools.antisuplantacion import generate_domain_variants, registrable_domain
from tools.asset_verification import asset_autorizado_para_escanear

logger = logging.getLogger("vigia.certstream")

_listener_thread: threading.Thread | None = None
_stop_event = threading.Event()
_variant_map: dict[str, tuple[str, str, str]] = {}
_variant_map_lock = threading.Lock()
_variant_map_built_at: float = 0.0

_DEFAULT_URL = "wss://certstream.calidog.io"


def _refresh_seconds() -> float:
    return float(os.environ.get("VIGIA_CERTSTREAM_REFRESH_MINUTES", "30")) * 60


def _build_variant_map() -> dict[str, tuple[str, str, str]]:
    """dominio_variante -> (tenant_id, asset_id, dominio_base), para todos los tenants.

    Solo considera activos verificados (o exentos por ser localhost/IP
    privada, aunque eso no tenga mucho sentido práctico para un dominio
    público -- ver `tools/asset_verification.py`) -- mismo gate que ahora
    aplica a `POST /scan`/`POST /scan/activo` (Item transversal de esta
    sesión: "gatear... antisuplantacion monitoring en verificado=true").
    Antes de este cambio, un tenant podía registrar el dominio de un
    tercero real y CertStream habría vigilado activamente variantes de ese
    dominio ajeno en su nombre -- monitoreo continuo no autorizado, el
    mismo tipo de gap que el resto de esta sesión cierra para el escaneo.
    """
    conn = get_conn()
    try:
        activos = conn.execute(
            "SELECT id, tenant_id, valor, verificado FROM assets WHERE tipo = 'dominio' AND is_active = 1"
        ).fetchall()
    finally:
        conn.close()

    mapa: dict[str, tuple[str, str, str]] = {}
    omitidos = 0
    for activo in activos:
        base = activo["valor"]
        if not asset_autorizado_para_escanear("dominio", base, bool(activo["verificado"])):
            omitidos += 1
            continue
        try:
            variantes = generate_domain_variants(base)
        except Exception:
            logger.exception("No se pudieron generar variantes dnstwist para el activo %s", base)
            continue
        for variante in variantes:
            mapa[variante] = (activo["tenant_id"], activo["id"], base)
    if omitidos:
        logger.info(
            "%d activo(s) de dominio omitidos del mapa de CertStream por no estar verificados ni exentos",
            omitidos,
        )
    return mapa


def _ensure_variant_map(force: bool = False) -> dict[str, tuple[str, str, str]]:
    global _variant_map, _variant_map_built_at
    with _variant_map_lock:
        if force or (time.time() - _variant_map_built_at) > _refresh_seconds():
            nuevo_mapa = _build_variant_map()
            _variant_map = nuevo_mapa
            _variant_map_built_at = time.time()
            logger.info(
                "Mapa de variantes CertStream (re)construido: %d variante(s) sobre %d dominio(s) vigilados",
                len(_variant_map),
                len({v[2] for v in _variant_map.values()}),
            )
        return _variant_map


def registrar_finding_certstream(
    tenant_id: str,
    asset_id: str,
    dominio_base: str,
    dominio_observado: str,
    cert_info: dict,
) -> str:
    """Escribe una fila `scans` sintética + una fila `findings` real para un match de CertStream.

    Sigue exactamente el patrón de inserción de `findings` usado en
    `api/main.py` (`scan()` / `_correr_escaneo_activo_en_background`):
    mismas columnas (id, scan_id, tenant_id, tipo, severidad, endpoint,
    confirmado, raw_json), mismo estilo de `raw_json` con el objeto crudo
    completo para trazabilidad. `confirmado=0` a propósito — un match de
    permutación de dominio es una señal fuerte, no una confirmación
    determinista de phishing activo (podría ser una empresa legítima con
    nombre parecido, o el propio cliente registrando una variante
    defensiva) — el mismo criterio que ya usa `agents/antisuplantacion.py`
    al pedirle a Claude que evalúe "suplantación real vs. coincidencia".

    Devuelve el `scan_id` sintético creado (usado por los tests/CLI de
    verificación de este módulo).
    """
    scan_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO scans
                (id, tenant_id, asset_id, target, autorizacion_firmada, estado,
                 reporte_final, trace_log_json, completed_at)
            VALUES (?, ?, ?, ?, 0, 'completado', ?, ?, ?)
            """,
            (
                scan_id,
                tenant_id,
                asset_id,
                dominio_base,
                f"CertStream detectó un dominio posible-clon de {dominio_base}: {dominio_observado}",
                json.dumps(
                    [
                        {
                            "agente": "certstream_listener",
                            "accion": f"certificado_emitido(dominio={dominio_observado!r})",
                            "resultado": f"coincide con variante dnstwist de {dominio_base}",
                        }
                    ]
                ),
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json)
            VALUES (?, ?, ?, 'dominio_variante_certstream', 'high', ?, 0, ?)
            """,
            (
                str(uuid.uuid4()),
                scan_id,
                tenant_id,
                dominio_observado,
                json.dumps(
                    {
                        "dominio_base": dominio_base,
                        "dominio_observado": dominio_observado,
                        "fuente": "certstream",
                        "cert": cert_info,
                    },
                    default=str,
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    logger.warning(
        "CertStream: posible dominio clon detectado — base=%s observado=%s tenant=%s scan_id=%s",
        dominio_base,
        dominio_observado,
        tenant_id,
        scan_id,
    )
    return scan_id


def _extraer_dominios(message: dict) -> list[str]:
    data = message.get("data") or {}
    leaf = data.get("leaf_cert") or {}
    dominios = leaf.get("all_domains") or []
    limpios: list[str] = []
    for d in dominios:
        d = (d or "").strip().lower()
        if d.startswith("*."):
            d = d[2:]
        if d and d not in limpios:
            limpios.append(d)
    return limpios


def procesar_mensaje_certstream(message: dict, context: object = None) -> list[str]:
    """Callback real que le pasamos a `certstream.listen_for_events`.

    Separado de `_run_listener` a propósito: es la unidad que se prueba
    sin websocket real, alimentándole un mensaje sintético con la forma
    exacta de un mensaje CertStream (ver `eval/live_run_report.md`, Item 5,
    para la corrida de verificación real hecha con esta función).

    Devuelve la lista de `scan_id` sintéticos creados (vacía si no hubo
    match) — solo para que la verificación manual/tests puedan confirmar
    qué se escribió sin tener que volver a consultar la base de datos.
    """
    if message.get("message_type") != "certificate_update":
        return []

    mapa = _ensure_variant_map()
    if not mapa:
        return []

    cert_info = (message.get("data") or {}).get("leaf_cert") or {}
    scan_ids: list[str] = []
    for dominio in _extraer_dominios(message):
        apex = registrable_domain(dominio)
        match = mapa.get(apex) or mapa.get(dominio)
        if not match:
            continue
        tenant_id, asset_id, dominio_base = match
        if apex == dominio_base or dominio == dominio_base:
            continue  # el propio dominio legítimo del cliente, no es un hallazgo
        try:
            scan_id = registrar_finding_certstream(tenant_id, asset_id, dominio_base, dominio, cert_info)
            scan_ids.append(scan_id)
        except Exception:
            logger.exception("No se pudo escribir el finding de CertStream para %s", dominio)
    return scan_ids


def _run_listener() -> None:
    try:
        import certstream
    except ImportError:
        logger.warning(
            "Paquete 'certstream' no instalado — listener de CertStream deshabilitado "
            "(pip install certstream). El resto de la API sigue funcionando normal."
        )
        return

    try:
        _ensure_variant_map(force=True)
    except Exception:
        logger.exception(
            "No se pudo construir el mapa inicial de variantes de dominio — "
            "listener de CertStream deshabilitado, el resto de la API sigue arriba."
        )
        return

    url = os.environ.get("VIGIA_CERTSTREAM_URL", _DEFAULT_URL)
    logger.info("Listener de CertStream iniciado (%s)", url)

    while not _stop_event.is_set():
        try:
            certstream.listen_for_events(procesar_mensaje_certstream, url=url, setup_logger=False)
        except Exception:
            if _stop_event.is_set():
                break
            logger.exception("Conexión CertStream perdida/fallida, reintentando en 30s")
            _stop_event.wait(30)


def start_certstream_listener() -> threading.Thread | None:
    """Arranca el listener en un daemon thread (idempotente — llamar dos veces no duplica hilos).

    Guardas de degradación (todas antes de tocar la red):
    - `VIGIA_CERTSTREAM_ENABLED=false` lo desactiva explícitamente.
    - Si el paquete `certstream` falta, `_run_listener` lo detecta y
      retorna sin lanzar (ver arriba) — el hilo muere solo, la API sigue.

    Nunca propaga una excepción al llamador (`api/main.py::lifespan`):
    cualquier fallo de arranque queda contenido dentro del thread.
    """
    global _listener_thread
    if _listener_thread is not None and _listener_thread.is_alive():
        return _listener_thread
    if os.environ.get("VIGIA_CERTSTREAM_ENABLED", "true").strip().lower() in ("0", "false", "no"):
        logger.info("Listener de CertStream deshabilitado por VIGIA_CERTSTREAM_ENABLED")
        return None

    _stop_event.clear()
    _listener_thread = threading.Thread(target=_run_listener, daemon=True, name="vigia-certstream")
    _listener_thread.start()
    return _listener_thread


def stop_certstream_listener() -> None:
    _stop_event.set()
