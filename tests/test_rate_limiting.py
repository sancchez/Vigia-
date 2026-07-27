"""Rate limiting de la API (slowapi) y cortesía de red al sondear dominios
de terceros (`tools/antisuplantacion.py`).

Los dos son controles de VOLUMEN, no de permiso: sin ellos, la política de
uso aceptable que se publica junto a este producto sería falsa. La suite
corre con el límite HTTP apagado por defecto (ver tests/conftest.py y el
porqué); este archivo lo reactiva a propósito para el único test que lo
necesita, y lo vuelve a apagar al terminar.
"""

from __future__ import annotations

import threading
import time

import pytest

import api.main as main_module
import tools.antisuplantacion as antisuplantacion
import tools.zap_api as zap_api


# ---------------------------------------------------------------------------
# 1. Rate limiting HTTP
# ---------------------------------------------------------------------------


def test_slowapi_registrado_en_la_app():
    """Aun si el paquete faltara (import degradado), `app.state.limiter` debe
    existir para que el resto del código no tenga que preguntar."""
    assert getattr(main_module.app.state, "limiter", None) is not None
    assert hasattr(main_module.limiter, "limit")


def test_clave_es_el_tenant_cuando_hay_jwt(client, monkeypatch):
    """El límite se cuenta por tenant (no por usuario ni por IP) cuando el
    request viene autenticado -- ver `clave_rate_limit`."""
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": "Tenant Rate", "email": "rate@limit.test", "password": "claveSegura123"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    tenant_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["tenant_id"]

    class _ReqFalso:
        headers = {"authorization": f"Bearer {token}"}
        client = None

    assert main_module.clave_rate_limit(_ReqFalso()) == f"tenant:{tenant_id}"


def test_sin_jwt_la_clave_cae_a_la_ip():
    class _ReqSinToken:
        headers = {}

        class client:  # noqa: N801 -- imita `starlette.Request.client`
            host = "203.0.113.9"

    assert main_module.clave_rate_limit(_ReqSinToken()) == "ip:203.0.113.9"


@pytest.mark.skipif(not main_module.SLOWAPI_DISPONIBLE, reason="slowapi no instalado")
def test_endpoint_caro_devuelve_429_al_exceder_el_limite(client, monkeypatch):
    """`POST /antisuplantacion` con el límite encendido y bajado a 2/minuto:
    la tercera llamada del mismo tenant debe cortarse con 429 ANTES de tocar
    ningún dominio de terceros."""
    from slowapi import Limiter

    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": "Tenant 429", "email": "t429@limit.test", "password": "claveSegura123"},
    )
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Limiter propio con un límite bajo y almacenamiento limpio, para no
    # depender de los contadores del limiter global ni contaminarlos.
    limiter_test = Limiter(key_func=main_module.clave_rate_limit, default_limits=["2/minute"])
    monkeypatch.setattr(main_module.app.state, "limiter", limiter_test, raising=False)

    codigos = []
    for _ in range(4):
        # asset_id inexistente -> 404 mientras haya cupo; lo que importa es
        # cuándo aparece el 429, no el cuerpo de la respuesta.
        r = client.post("/antisuplantacion", json={"asset_id": "no-existe"}, headers=headers)
        codigos.append(r.status_code)

    assert 429 in codigos, f"nunca se aplicó el límite: {codigos}"


# ---------------------------------------------------------------------------
# 2. Cortesía de red al sondear dominios lookalike de terceros
# ---------------------------------------------------------------------------


def test_user_agent_del_bot_es_identificable():
    """Un bot atribuible es defensa legal real: tiene que decir quién es y a
    dónde escribirle."""
    ua = antisuplantacion.USER_AGENT_BOT
    assert "Vigia" in ua
    assert "+http" in ua  # URL de contacto/política, convención de bots


def test_el_sondeo_http_manda_el_user_agent_del_bot(monkeypatch):
    capturado = {}

    def _urlopen_falso(peticion, timeout=None):
        capturado["ua"] = peticion.get_header("User-agent")
        raise RuntimeError("no importa la respuesta")

    monkeypatch.setattr(antisuplantacion.urllib.request, "urlopen", _urlopen_falso)
    antisuplantacion._probe_http("clon.test", 1)
    assert capturado["ua"] == antisuplantacion.USER_AGENT_BOT


def test_sondeos_consecutivos_respetan_el_delay_minimo(monkeypatch):
    """Dos sondeos a hosts DISTINTOS no pueden salir pegados: el segundo
    espera el delay global configurado."""
    monkeypatch.setenv("VIGIA_SONDEO_DELAY_SEGUNDOS", "0.4")
    monkeypatch.setenv("VIGIA_SONDEO_DELAY_MISMO_HOST", "0")
    monkeypatch.setattr(antisuplantacion, "_ultimo_sondeo_global", 0.0)
    monkeypatch.setattr(antisuplantacion, "_ultimo_sondeo_por_host", {})

    inicio = time.monotonic()
    for host in ("a.test", "b.test", "c.test"):
        with antisuplantacion.turno_de_sondeo(host):
            pass
    transcurrido = time.monotonic() - inicio

    # Tres turnos = al menos dos esperas de 0.4s (el primero puede salir ya).
    assert transcurrido >= 0.8


def test_el_mismo_host_espera_mas_que_hosts_distintos(monkeypatch):
    monkeypatch.setenv("VIGIA_SONDEO_DELAY_SEGUNDOS", "0")
    monkeypatch.setenv("VIGIA_SONDEO_DELAY_MISMO_HOST", "0.5")
    monkeypatch.setattr(antisuplantacion, "_ultimo_sondeo_global", 0.0)
    monkeypatch.setattr(antisuplantacion, "_ultimo_sondeo_por_host", {})

    with antisuplantacion.turno_de_sondeo("victima.test"):
        pass
    inicio = time.monotonic()
    with antisuplantacion.turno_de_sondeo("victima.test"):
        pass
    assert (time.monotonic() - inicio) >= 0.5


def test_concurrencia_de_sondeo_acotada(monkeypatch):
    """Con tope 1, dos hilos no pueden estar sondeando a la vez."""
    monkeypatch.setenv("VIGIA_SONDEO_DELAY_SEGUNDOS", "0")
    monkeypatch.setenv("VIGIA_SONDEO_DELAY_MISMO_HOST", "0")
    monkeypatch.setenv("VIGIA_SONDEO_MAX_CONCURRENTE", "1")
    monkeypatch.setattr(antisuplantacion, "_semaforo_sondeo", None)
    monkeypatch.setattr(antisuplantacion, "_semaforo_sondeo_tope", 0)

    activos = 0
    pico = 0
    candado = threading.Lock()

    def _sondear(host: str) -> None:
        nonlocal activos, pico
        with antisuplantacion.turno_de_sondeo(host):
            with candado:
                activos += 1
                pico = max(pico, activos)
            time.sleep(0.05)
            with candado:
                activos -= 1

    hilos = [threading.Thread(target=_sondear, args=(f"h{i}.test",)) for i in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert pico == 1


def test_puntuar_infra_pasa_por_el_turno_de_sondeo(monkeypatch):
    """Regresión: el scoring completo debe estar DENTRO del gate de cortesía,
    no saltárselo."""
    llamadas = []

    import contextlib

    @contextlib.contextmanager
    def _turno_espia(host):
        llamadas.append(host)
        yield

    monkeypatch.setattr(antisuplantacion, "turno_de_sondeo", _turno_espia)
    monkeypatch.setattr(antisuplantacion, "_probe_dns_a", lambda *_a: False)
    monkeypatch.setattr(antisuplantacion, "_probe_mx", lambda *_a: False)
    monkeypatch.setattr(antisuplantacion, "_probe_ssl", lambda *_a: (False, None))
    monkeypatch.setattr(antisuplantacion, "_probe_http", lambda *_a: False)

    antisuplantacion.puntuar_infra_lookalike("clon.test")
    assert llamadas == ["clon.test"]


# ---------------------------------------------------------------------------
# 3. Tope de escaneos activos ZAP simultáneos
# ---------------------------------------------------------------------------


def test_cupo_de_escaneos_activos_es_configurable(monkeypatch):
    monkeypatch.setenv("VIGIA_ZAP_MAX_CONCURRENTES", "3")
    monkeypatch.setenv("VIGIA_ZAP_ESPERA_SLOT_SEGUNDOS", "0")
    assert zap_api._config_cupo_escaneos() == (3, 0.0)


def test_escaneo_activo_extra_falla_con_error_explicito(monkeypatch):
    """Con tope 1 y espera 0, el segundo escaneo simultáneo no arranca ningún
    contenedor: falla con `ZapDaemonError` legible."""
    monkeypatch.setenv("VIGIA_ZAP_MAX_CONCURRENTES", "1")
    monkeypatch.setenv("VIGIA_ZAP_ESPERA_SLOT_SEGUNDOS", "0")
    monkeypatch.setattr(zap_api, "_semaforo_escaneos", None)
    monkeypatch.setattr(zap_api, "_semaforo_escaneos_tope", 0)

    with zap_api.turno_de_escaneo_activo("primero"):
        with pytest.raises(zap_api.ZapDaemonError) as exc:
            with zap_api.turno_de_escaneo_activo("segundo"):
                pass
        assert "Cupo de escaneos activos simultáneos agotado" in str(exc.value)

    # Liberado el primero, vuelve a haber cupo.
    with zap_api.turno_de_escaneo_activo("tercero"):
        pass
