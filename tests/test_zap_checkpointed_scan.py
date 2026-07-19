"""`tools/zap_api.py` -- escaneo activo checkpointed vía la API real de ZAP.

Ver HANDOFF.md Item 2: reemplaza el `subprocess.run(..., timeout=...)`
monolítico (bloqueo de hasta 35 min sin visibilidad intermedia, ver
eval/live_run_report.md Corridas 4 y 12) por un daemon de ZAP + polling
HTTP corto e incremental. Verificado en vivo esta sesión contra Juice Shop
real (progreso real de spider/AJAX spider/escaneo activo observado a
través de `GET /scans/{id}` real, no solo en este módulo aislado) -- ver
el reporte de la corrida para el detalle de esa verificación.

Estos tests son deterministas y rápidos (mockean todas las llamadas de red
y de Docker) -- cubren dos bugs reales encontrados en la verificación en
vivo de esta sesión, no solo el camino feliz:

1. El contenedor SIEMPRE se limpia (`stop_daemon`), tanto en éxito como en
   cualquier excepción -- mismo principio que el fix de Bug 1.
2. El reloj del presupuesto de `minutes` arranca DESPUÉS de que el daemon
   esté listo, no desde el arranque del contenedor -- bug real: contando
   desde el arranque, el tiempo de instalación de add-ons (45s+ en un host
   tranquilo, más de 120s confirmado bajo contención real de Docker en
   esta sesión) le comía presupuesto al escaneo real sin que el usuario lo
   pidiera.
3. `_zap_get` reintenta errores de red transitorios -- bug real encontrado
   en la verificación en vivo: un único `ReadTimeout` durante el AJAX
   Spider (la fase más pesada, levanta Firefox headless) tumbaba el
   escaneo activo completo aunque el daemon seguía perfectamente vivo.
"""

from __future__ import annotations

import pytest

import tools.zap_api as zap_api


@pytest.fixture(autouse=True)
def _sin_docker_real(monkeypatch):
    monkeypatch.setattr(zap_api, "require_binary", lambda *_a, **_k: "docker")
    monkeypatch.setattr(zap_api.time, "sleep", lambda _s: None)


def _mockear_ciclo_completo(monkeypatch, *, spider_pcts=(100,), ascan_pcts=(100,), ajax=False):
    """Mockea start_daemon/wait_ready/spider/ascan/alerts/stop_daemon con
    secuencias de progreso controladas -- sin red ni Docker reales."""
    llamadas = {"stop_daemon": [], "spider_polls": 0, "ascan_polls": 0}

    monkeypatch.setattr(zap_api, "start_daemon", lambda name: (f"http://fake/{name}", 1))
    monkeypatch.setattr(zap_api, "wait_ready", lambda base_url, **_k: None)

    spider_iter = iter(spider_pcts)
    ascan_iter = iter(ascan_pcts)

    monkeypatch.setattr(zap_api, "spider_start", lambda *_a: "spider-0")

    def _spider_status(*_a):
        llamadas["spider_polls"] += 1
        return next(spider_iter, 100)

    monkeypatch.setattr(zap_api, "spider_status", _spider_status)

    monkeypatch.setattr(zap_api, "ajax_spider_start", lambda *_a: None)
    monkeypatch.setattr(zap_api, "ajax_spider_status", lambda *_a: "stopped")
    monkeypatch.setattr(zap_api, "ajax_spider_results_count", lambda *_a: 0)
    monkeypatch.setattr(zap_api, "ajax_spider_stop", lambda *_a: None)

    monkeypatch.setattr(zap_api, "ascan_start", lambda *_a: "ascan-0")

    def _ascan_status(*_a):
        llamadas["ascan_polls"] += 1
        return next(ascan_iter, 100)

    monkeypatch.setattr(zap_api, "ascan_status", _ascan_status)
    monkeypatch.setattr(zap_api, "ascan_stop", lambda *_a: None)
    monkeypatch.setattr(zap_api, "add_bearer_auth_replacer", lambda *_a: None)
    monkeypatch.setattr(zap_api, "get_alerts", lambda *_a: [{"name": "XSS reflejado", "riskdesc": "Medium"}])
    monkeypatch.setattr(
        zap_api, "stop_daemon", lambda name: llamadas["stop_daemon"].append(name)
    )
    return llamadas


def test_camino_feliz_reporta_fases_y_limpia_contenedor(monkeypatch):
    llamadas = _mockear_ciclo_completo(monkeypatch)
    progresos = []

    resultado = zap_api.run_checkpointed_active_scan(
        "http://example.test",
        minutes=5,
        bearer_token=None,
        ajax_spider=False,
        scan_id="camino-feliz",
        on_progress=progresos.append,
        poll_interval=0,
    )

    assert resultado.findings == [{"name": "XSS reflejado", "riskdesc": "Medium"}]
    assert resultado.fases_completadas == ["spider", "ascan"]
    assert llamadas["stop_daemon"] == ["vigia-zap-camino-feliz"]
    fases_reportadas = {p.fase for p in progresos}
    assert "iniciando" in fases_reportadas
    assert "spider" in fases_reportadas
    assert "ascan" in fases_reportadas
    assert "completado" in fases_reportadas


def test_ajax_spider_corre_cuando_se_pide(monkeypatch):
    llamadas = _mockear_ciclo_completo(monkeypatch, ajax=True)
    progresos = []

    resultado = zap_api.run_checkpointed_active_scan(
        "http://example.test",
        minutes=5,
        bearer_token=None,
        ajax_spider=True,
        scan_id="con-ajax",
        on_progress=progresos.append,
        poll_interval=0,
    )

    assert "ajax_spider" in resultado.fases_completadas
    assert any(p.fase == "ajax_spider" for p in progresos)
    assert llamadas["stop_daemon"] == ["vigia-zap-con-ajax"]


def test_contenedor_se_limpia_incluso_si_falla_a_mitad(monkeypatch):
    """El contenedor daemon SIEMPRE se limpia -- mismo principio que Bug 1:
    nunca dejar un contenedor huérfano, ni siquiera cuando algo revienta."""
    llamadas = _mockear_ciclo_completo(monkeypatch)

    def _spider_status_rompe(*_a):
        raise RuntimeError("la API de ZAP se cayó a mitad del spider")

    monkeypatch.setattr(zap_api, "spider_status", _spider_status_rompe)

    with pytest.raises(RuntimeError):
        zap_api.run_checkpointed_active_scan(
            "http://example.test",
            minutes=5,
            bearer_token=None,
            ajax_spider=False,
            scan_id="falla-a-mitad",
            poll_interval=0,
        )

    assert llamadas["stop_daemon"] == ["vigia-zap-falla-a-mitad"]


def test_daemon_nunca_listo_limpia_contenedor_y_propaga(monkeypatch):
    llamadas = {"stop_daemon": []}
    monkeypatch.setattr(zap_api, "start_daemon", lambda name: (f"http://fake/{name}", 1))

    def _wait_ready_falla(*_a, **_k):
        raise zap_api.ZapDaemonError("el daemon nunca respondió")

    monkeypatch.setattr(zap_api, "wait_ready", _wait_ready_falla)
    monkeypatch.setattr(
        zap_api, "stop_daemon", lambda name: llamadas["stop_daemon"].append(name)
    )

    with pytest.raises(zap_api.ZapDaemonError):
        zap_api.run_checkpointed_active_scan(
            "http://example.test",
            minutes=5,
            bearer_token=None,
            ajax_spider=False,
            scan_id="nunca-listo",
            poll_interval=0,
        )

    assert llamadas["stop_daemon"] == ["vigia-zap-nunca-listo"]


def test_presupuesto_no_cuenta_el_tiempo_de_arranque_del_daemon(monkeypatch):
    """Bug real encontrado en la verificación en vivo: si el reloj del
    presupuesto arrancara ANTES de `wait_ready`, un arranque lento (add-ons
    reinstalándose, confirmado en esta sesión que puede superar 120s bajo
    contención real de Docker) se comería todo el `minutes` sin que el
    escaneo real llegara a correr un solo poll -- y aun así reportaría
    'completado' con 0 hallazgos, engañosamente. Se simula un arranque
    "lento" haciendo que `wait_ready` avance el reloj monotónico más allá
    de todo el presupuesto de `minutes=1` (60s) antes de devolver."""
    llamadas = _mockear_ciclo_completo(monkeypatch)

    reloj = {"t": 0.0}
    monkeypatch.setattr(zap_api.time, "monotonic", lambda: reloj["t"])

    def _wait_ready_lento(*_a, **_k):
        reloj["t"] += 200.0  # más que el presupuesto total de minutes=1 (60s)

    monkeypatch.setattr(zap_api, "wait_ready", _wait_ready_lento)

    resultado = zap_api.run_checkpointed_active_scan(
        "http://example.test",
        minutes=1,
        bearer_token=None,
        ajax_spider=False,
        scan_id="arranque-lento",
        poll_interval=0,
    )

    # "ascan" solo se marca si _tiempo_restante() > 0 justo antes de
    # arrancarlo (ver run_checkpointed_active_scan) -- si el bug estuviera
    # presente (reloj arrancando antes de wait_ready), ese chequeo fallaría
    # de inmediato tras el "arranque lento" simulado y "ascan" jamás
    # aparecería en fases_completadas.
    assert "spider" in resultado.fases_completadas
    assert "ascan" in resultado.fases_completadas
    assert llamadas["stop_daemon"] == ["vigia-zap-arranque-lento"]


# ---------------------------------------------------------------------------
# _zap_get -- reintentos ante errores de red transitorios (ReadTimeout real
# observado en la verificación en vivo de esta sesión durante el AJAX Spider).
# ---------------------------------------------------------------------------


class _RespuestaFalsa:
    def raise_for_status(self):
        return None

    def json(self):
        return {"status": "100"}


def test_zap_get_reintenta_y_se_recupera_de_un_timeout_transitorio(monkeypatch):
    import requests

    intentos = {"n": 0}

    def _get_falla_una_vez_luego_ok(*_a, **_k):
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise requests.exceptions.ReadTimeout("timeout transitorio simulado")
        return _RespuestaFalsa()

    monkeypatch.setattr(requests, "get", _get_falla_una_vez_luego_ok)

    resultado = zap_api._zap_get("http://fake", "/JSON/spider/view/status/", retries=2)

    assert resultado == {"status": "100"}
    assert intentos["n"] == 2


def test_zap_get_propaga_si_todos_los_reintentos_fallan(monkeypatch):
    import requests

    def _siempre_falla(*_a, **_k):
        raise requests.exceptions.ReadTimeout("daemon genuinamente caído")

    monkeypatch.setattr(requests, "get", _siempre_falla)

    with pytest.raises(requests.exceptions.ReadTimeout):
        zap_api._zap_get("http://fake", "/JSON/spider/view/status/", retries=2)
