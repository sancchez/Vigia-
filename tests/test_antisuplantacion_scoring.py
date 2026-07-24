"""`tools/antisuplantacion.py::puntuar_infra_lookalike`/`capturar_screenshot`
(Fase 3a, openspec/changes/fase3a-antisuplantacion-y-scoring).

Cada probe (`_probe_dns_a`/`_probe_mx`/`_probe_ssl`/`_probe_http`) se mockea
por separado -- no se hace resolución DNS/SSL/HTTP real contra dominios de
terceros en la suite de tests (mismo principio que el resto del proyecto:
sin red real en `pytest tests/`). El objetivo de estos tests es la fórmula
de scoring (`score = sum(4 booleanos)`) y la degradación con gracia cuando
una señal individual no está disponible -- ver spec.md, Domain "Scoring de
infraestructura activa".
"""

from __future__ import annotations

import tools.antisuplantacion as antisuplantacion


def _mockear_probes(monkeypatch, *, dns_a: bool, mx: bool, ssl_ok: bool, http_ok: bool, ssl_not_before=None):
    monkeypatch.setattr(antisuplantacion, "_probe_dns_a", lambda _d, _t: dns_a)
    monkeypatch.setattr(antisuplantacion, "_probe_mx", lambda _d, _t: mx)
    monkeypatch.setattr(antisuplantacion, "_probe_ssl", lambda _d, _t: (ssl_ok, ssl_not_before))
    monkeypatch.setattr(antisuplantacion, "_probe_http", lambda _d, _t: http_ok)


def test_dominio_con_las_cuatro_senales_activas_da_score_4(monkeypatch):
    _mockear_probes(
        monkeypatch, dns_a=True, mx=True, ssl_ok=True, http_ok=True, ssl_not_before="2026-01-01T00:00:00+00:00"
    )
    resultado = antisuplantacion.puntuar_infra_lookalike("clon-malicioso.test")
    assert resultado.score == 4
    assert resultado.dns_a and resultado.mx and resultado.ssl and resultado.http
    assert resultado.ssl_not_before == "2026-01-01T00:00:00+00:00"


def test_dominio_parqueado_solo_con_dns_da_score_1(monkeypatch):
    _mockear_probes(monkeypatch, dns_a=True, mx=False, ssl_ok=False, http_ok=False)
    resultado = antisuplantacion.puntuar_infra_lookalike("clon-parqueado.test")
    assert resultado.score == 1


def test_dominio_sin_ninguna_senal_da_score_0(monkeypatch):
    _mockear_probes(monkeypatch, dns_a=False, mx=False, ssl_ok=False, http_ok=False)
    resultado = antisuplantacion.puntuar_infra_lookalike("clon-inexistente.test")
    assert resultado.score == 0


def test_todas_las_combinaciones_de_senales_dan_el_score_correcto(monkeypatch):
    """Recorre las 16 combinaciones posibles de las 4 señales booleanas --
    la fórmula es una cuenta plana, sin pesos, así que el score debe ser
    exactamente la cantidad de señales en `True`."""
    for combinacion in range(16):
        dns_a = bool(combinacion & 1)
        mx = bool(combinacion & 2)
        ssl_ok = bool(combinacion & 4)
        http_ok = bool(combinacion & 8)
        _mockear_probes(monkeypatch, dns_a=dns_a, mx=mx, ssl_ok=ssl_ok, http_ok=http_ok)
        resultado = antisuplantacion.puntuar_infra_lookalike("dominio-cualquiera.test")
        assert resultado.score == sum([dns_a, mx, ssl_ok, http_ok])


def test_probe_mx_sin_dnspython_instalado_cuenta_false_sin_romper(monkeypatch):
    """`_probe_mx` real (no mockeado) debe degradar con gracia si `dns.resolver`
    no se puede importar -- simula el ImportError sin necesitar desinstalar
    dnspython de verdad."""
    import builtins

    real_import = builtins.__import__

    def _import_que_bloquea_dns(nombre, *args, **kwargs):
        if nombre == "dns.resolver" or nombre.startswith("dns."):
            raise ImportError(f"simulado: {nombre} no instalado")
        return real_import(nombre, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import_que_bloquea_dns)
    assert antisuplantacion._probe_mx("cualquier-dominio.test", timeout=1) is False


def test_scoring_completo_degrada_sin_romper_cuando_mx_no_disponible(monkeypatch):
    """Escenario de integración de la degradación: aunque el probe MX real
    lance ImportError, las demás señales se calculan igual y el score total
    solo excluye esa señal (spec.md: 'Probe MX no disponible degrada, no
    rompe')."""
    monkeypatch.setattr(antisuplantacion, "_probe_dns_a", lambda _d, _t: True)
    monkeypatch.setattr(antisuplantacion, "_probe_mx", lambda _d, _t: False)
    monkeypatch.setattr(antisuplantacion, "_probe_ssl", lambda _d, _t: (True, None))
    monkeypatch.setattr(antisuplantacion, "_probe_http", lambda _d, _t: True)
    resultado = antisuplantacion.puntuar_infra_lookalike("clon-sin-mx.test")
    assert resultado.score == 3
    assert resultado.mx is False


def test_capturar_screenshot_sin_playwright_instalado_devuelve_none(monkeypatch, tmp_path):
    """Simula Playwright no instalado (ImportError en el import diferido
    dentro de la función) -- debe devolver `None`, nunca lanzar."""
    import builtins

    real_import = builtins.__import__

    def _import_que_bloquea_playwright(nombre, *args, **kwargs):
        if nombre == "playwright.sync_api" or nombre.startswith("playwright"):
            raise ImportError("simulado: playwright no instalado")
        return real_import(nombre, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import_que_bloquea_playwright)
    resultado = antisuplantacion.capturar_screenshot("http://clon.test", str(tmp_path))
    assert resultado is None


def test_capturar_screenshot_maneja_fallo_de_captura_sin_romper(monkeypatch, tmp_path):
    """Aunque Playwright esté "instalado" (mockeado), un fallo real durante
    la navegación/captura (timeout, DNS) debe devolver `None`, no propagar
    la excepción."""
    import sys
    import types

    modulo_falso = types.ModuleType("playwright.sync_api")

    class _SyncPlaywrightQueFalla:
        def __enter__(self):
            raise RuntimeError("simulado: no se pudo lanzar el navegador")

        def __exit__(self, *args):
            return False

    modulo_falso.sync_playwright = lambda: _SyncPlaywrightQueFalla()
    monkeypatch.setitem(sys.modules, "playwright.sync_api", modulo_falso)

    resultado = antisuplantacion.capturar_screenshot("http://clon.test", str(tmp_path))
    assert resultado is None
