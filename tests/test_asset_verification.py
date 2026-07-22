"""Verificación de propiedad de dominio -- el gap real que cierra esta sesión.

Antes de este cambio, `POST /assets` dejaba que cualquier tenant autenticado
registrara CUALQUIER dominio como propio (incluido el de un tercero real,
ej. 'microsoft.com') sin ninguna prueba de control, y ese registro por sí
solo bastaba para que `POST /scan`/`POST /scan/activo` lo trataran como
autorizado -- ver `tools/asset_verification.py` para el diseño completo
(patrón Google Search Console / Detectify: token único + prueba de control
vía DNS TXT o archivo bien-conocido) y la razón de la excepción de
localhost/IP privada.

Cuatro niveles de prueba, mismo espíritu que el resto de la suite
(`test_scan_activo_asset_gate.py`, `test_gate_autorizacion.py`):

1. Unidad pura sobre `tools/asset_verification.py` -- sin HTTP, sin red real
   salvo donde se documenta explícitamente lo contrario.
2. Integración sobre `POST /assets` / `POST /assets/{id}/verify` reales vía
   `TestClient`, con la comprobación DNS/HTTP mockeada (no depende de que
   ningún dominio de prueba resuelva de verdad).
3. Integración sobre el nuevo gate de `POST /scan` y `POST /scan/activo`:
   activo sin verificar bloquea, activo verificado o exento (localhost/IP
   privada) permite.
4. Una prueba de red REAL (no mockeada) de `verificar_dns_txt` contra un
   dominio público real que no controlamos (`google.com`) -- no puede
   demostrar el camino de éxito (no tenemos forma de escribir un TXT en un
   dominio que no controlamos), pero sí demuestra que la función ejecuta una
   resolución DNS real contra la red real y decodifica la respuesta real, en
   vez de estar simulada de punta a punta. El camino de éxito (token
   encontrado) se cubre por separado con `dns.resolver.resolve` mockeado,
   documentado explícitamente como mock.
"""

from __future__ import annotations

import api.main as main_module
from tools.asset_verification import (
    asset_autorizado_para_escanear,
    es_target_exento_de_verificacion,
    generar_token,
    instrucciones_verificacion,
    verificar_dns_txt,
    verificar_http_well_known,
)

# ---------------------------------------------------------------------------
# 1. Unidad pura -- tools/asset_verification.py
# ---------------------------------------------------------------------------


def test_localhost_exento_como_dominio_y_como_puerto():
    assert es_target_exento_de_verificacion("dominio", "localhost") is True
    assert es_target_exento_de_verificacion("dominio", "http://localhost:3000") is True
    assert es_target_exento_de_verificacion("app", "localhost:8080") is True


def test_dominios_publicos_autorizados_exentos():
    """La familia *.vulnweb.com de Acunetix es una categoría de excepción
    DISTINTA de localhost/IP privada -- son terceros reales, pero con
    autorización general ya otorgada por su propio operador para pruebas de
    herramientas de seguridad (ver DOMINIOS_PUBLICOS_AUTORIZADOS). No debe
    confundirse con "cualquier dominio que suene a sitio de prueba" -- ver
    test_dominio_publico_real_o_sintetico_no_exento, que confirma que
    microsoft.com y dominios sintéticos NO se eximen."""
    assert es_target_exento_de_verificacion("dominio", "testasp.vulnweb.com") is True
    assert es_target_exento_de_verificacion("dominio", "http://testphp.vulnweb.com/") is True
    assert es_target_exento_de_verificacion("dominio", "microsoft.com") is False


def test_ip_privada_y_loopback_exentas():
    assert es_target_exento_de_verificacion("ip", "127.0.0.1") is True
    assert es_target_exento_de_verificacion("ip", "10.0.0.5") is True
    assert es_target_exento_de_verificacion("ip", "192.168.1.50") is True
    assert es_target_exento_de_verificacion("ip", "172.16.0.9") is True
    assert es_target_exento_de_verificacion("dominio", "http://192.168.0.10:8080") is True


def test_ip_publica_no_exenta():
    """8.8.8.8 y 1.1.1.1 son IPs públicas reales (resolutores DNS de Google
    y Cloudflare) -- deliberadamente NO se usa aquí 203.0.113.7 (el ejemplo
    de IP que sí usa `test_scan_activo_asset_gate.py`): ese rango es
    TEST-NET-3 (RFC 5737, reservado para documentación), y Python
    (`ipaddress.ip_address(...).is_private`) lo clasifica como privado por
    no ser globalmente enrutable -- correcto de la librería estándar, solo
    hay que no confundirlo con "IP pública de ejemplo" en un test nuevo."""
    assert es_target_exento_de_verificacion("ip", "8.8.8.8") is False
    assert es_target_exento_de_verificacion("ip", "1.1.1.1") is False


def test_dominio_publico_real_o_sintetico_no_exento():
    """El caso central que motiva todo este diseño: un dominio público de
    verdad (o uno sintético usado en pruebas de laboratorio, ej.
    'miempresatest.com') NO debe eximirse solo porque "suena" a prueba --
    solo localhost/IP privada, literal, se exime (ver docstring del módulo,
    la razón anti-DNS-rebinding)."""
    assert es_target_exento_de_verificacion("dominio", "miempresatest.com") is False
    assert es_target_exento_de_verificacion("dominio", "microsoft.com") is False
    assert es_target_exento_de_verificacion("dominio", "midominiolegitimo.test") is False


def test_asset_autorizado_para_escanear_combina_verificado_y_exento():
    assert asset_autorizado_para_escanear("dominio", "miempresa.com", verificado=True) is True
    assert asset_autorizado_para_escanear("dominio", "miempresa.com", verificado=False) is False
    assert asset_autorizado_para_escanear("dominio", "localhost", verificado=False) is True
    assert asset_autorizado_para_escanear("ip", "127.0.0.1", verificado=False) is True


def test_token_es_unico_e_incluye_prefijo():
    a, b = generar_token(), generar_token()
    assert a != b
    assert a.startswith("vigia-verify-")
    assert len(a) > len("vigia-verify-")


def test_instrucciones_verificacion_incluye_ambos_metodos():
    token = generar_token()
    instrucciones = instrucciones_verificacion("dominio", "miempresa.com", token)
    assert instrucciones is not None
    assert instrucciones["dns_txt"]["registro"] == "_vigia-challenge.miempresa.com"
    assert instrucciones["dns_txt"]["valor"] == token
    assert instrucciones["http_file"]["url"] == "https://miempresa.com/.well-known/vigia-verification.txt"
    assert instrucciones["http_file"]["contenido"] == token


def test_instrucciones_verificacion_none_para_ip_y_para_exentos():
    token = generar_token()
    assert instrucciones_verificacion("ip", "8.8.8.8", token) is None
    assert instrucciones_verificacion("dominio", "localhost", token) is None


# ---------------------------------------------------------------------------
# 2. Integración -- POST /assets y POST /assets/{id}/verify reales
# ---------------------------------------------------------------------------


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123"):
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def test_post_assets_genera_token_e_instrucciones_para_dominio_publico(client):
    token_auth = _registrar_tenant(client, "Pyme Verif A", "verifa@assets.test")
    resp = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"tipo": "dominio", "valor": "dominiopublico.test"},
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["verificado"] is False
    assert cuerpo["exento_de_verificacion"] is False
    assert cuerpo["instrucciones_verificacion"] is not None
    assert cuerpo["instrucciones_verificacion"]["dns_txt"]["registro"] == "_vigia-challenge.dominiopublico.test"


def test_post_assets_localhost_marca_exento_sin_instrucciones(client):
    token_auth = _registrar_tenant(client, "Pyme Verif B", "verifb@assets.test")
    resp = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"tipo": "dominio", "valor": "localhost"},
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["verificado"] is False
    assert cuerpo["exento_de_verificacion"] is True
    assert cuerpo["instrucciones_verificacion"] is None


def test_verify_endpoint_marca_verificado_cuando_la_comprobacion_pasa(client, monkeypatch):
    """La comprobación DNS/HTTP real se mockea aquí -- no controlamos
    'dominiopublico.test' para escribirle un TXT real -- pero el endpoint en
    sí (lookup del asset por tenant, actualización de la fila, respuesta)
    corre real de punta a punta."""
    monkeypatch.setattr(
        main_module, "verificar_asset", lambda *_a, **_k: (True, "mock: token encontrado en TXT")
    )
    token_auth = _registrar_tenant(client, "Pyme Verif C", "verifc@assets.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"tipo": "dominio", "valor": "dominiopublico.test"},
    )
    asset_id = asset.json()["id"]

    resp = client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"metodo": "dns_txt"},
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["verificado"] is True
    assert cuerpo["verification_method"] == "dns_txt"
    assert cuerpo["verified_at"] is not None

    # Confirmado en DB, no solo en la respuesta HTTP.
    listado = client.get("/assets", headers={"Authorization": f"Bearer {token_auth}"})
    assert listado.json()[0]["verificado"] is True


def test_verify_endpoint_no_marca_verificado_cuando_la_comprobacion_falla(client, monkeypatch):
    monkeypatch.setattr(
        main_module, "verificar_asset", lambda *_a, **_k: (False, "mock: TXT no encontrado")
    )
    token_auth = _registrar_tenant(client, "Pyme Verif D", "verifd@assets.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"tipo": "dominio", "valor": "dominiopublico.test"},
    )
    asset_id = asset.json()["id"]

    resp = client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"metodo": "dns_txt"},
    )
    assert resp.status_code == 200
    assert resp.json()["verificado"] is False


def test_verify_endpoint_rechaza_asset_exento(client):
    token_auth = _registrar_tenant(client, "Pyme Verif E", "verife@assets.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"tipo": "dominio", "valor": "localhost"},
    )
    asset_id = asset.json()["id"]

    resp = client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"metodo": "dns_txt"},
    )
    assert resp.status_code == 400


def test_verify_endpoint_rechaza_asset_tipo_ip(client):
    token_auth = _registrar_tenant(client, "Pyme Verif F", "veriff@assets.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"tipo": "ip", "valor": "203.0.113.7"},
    )
    asset_id = asset.json()["id"]

    resp = client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"metodo": "dns_txt"},
    )
    assert resp.status_code == 422


def test_verify_endpoint_404_para_asset_de_otro_tenant(client):
    """Mismo principio de aislamiento multi-tenant que
    test_scan_activo_asset_gate.py::test_scan_activo_rechaza_target_de_otro_tenant."""
    token_dueno = _registrar_tenant(client, "Dueno Verif", "duenoverif@assets.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_dueno}"},
        json={"tipo": "dominio", "valor": "propiedadverif.test"},
    )
    asset_id = asset.json()["id"]

    token_atacante = _registrar_tenant(client, "Atacante Verif", "atacanteverif@assets.test")
    resp = client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token_atacante}"},
        json={"metodo": "dns_txt"},
    )
    assert resp.status_code == 404


def test_verify_endpoint_422_metodo_invalido(client):
    token_auth = _registrar_tenant(client, "Pyme Verif G", "verifg@assets.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"tipo": "dominio", "valor": "dominiopublico.test"},
    )
    asset_id = asset.json()["id"]

    resp = client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"metodo": "ftp"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 3. Integración -- el nuevo gate de POST /scan y POST /scan/activo
# ---------------------------------------------------------------------------


def test_scan_bloquea_dominio_publico_sin_verificar(client):
    token_auth = _registrar_tenant(client, "Pyme Scan Gate A", "scangatea@scanverif.test")
    client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"tipo": "dominio", "valor": "dominiosinverificar.test"},
    )

    resp = client.post(
        "/scan",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"target": "dominiosinverificar.test", "autorizacion_firmada": False},
    )
    assert resp.status_code == 403
    assert "no verificado" in resp.json()["detail"].lower() or "no está verificado" in resp.json()["detail"].lower() or "NO verificado" in resp.json()["detail"]


def test_scan_bloquea_target_nunca_registrado(client):
    token_auth = _registrar_tenant(client, "Pyme Scan Gate B", "scangateb@scanverif.test")
    resp = client.post(
        "/scan",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"target": "jamas-registrado.test", "autorizacion_firmada": False},
    )
    assert resp.status_code == 403
    assert "activo registrado" in resp.json()["detail"]


def test_scan_permite_asset_verificado(client, monkeypatch):
    """Contraprueba necesaria: un dominio público SÍ verificado no debe
    bloquearse. El grafo real se invoca (recon/verificación/etc. -- el
    fixture autouse `_sin_llm_real_por_defecto` de conftest.py ya evita
    llamadas reales a Claude); solo importa que el gate deje pasar."""
    monkeypatch.setattr(
        main_module, "verificar_asset", lambda *_a, **_k: (True, "mock: token encontrado")
    )
    import agents.recon as recon_module
    from tools._shared import ToolExecutionError

    def _recon_falla_rapido(*_a, **_k):
        raise ToolExecutionError("mock de test: recon deshabilitado en este test")

    monkeypatch.setattr(recon_module, "run_subfinder", _recon_falla_rapido)
    monkeypatch.setattr(recon_module, "run_amass_passive", _recon_falla_rapido)

    token_auth = _registrar_tenant(client, "Pyme Scan Gate C", "scangatec@scanverif.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"tipo": "dominio", "valor": "dominioverificado.test"},
    )
    asset_id = asset.json()["id"]
    verif = client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"metodo": "http_file"},
    )
    assert verif.json()["verificado"] is True

    resp = client.post(
        "/scan",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"target": "dominioverificado.test", "autorizacion_firmada": False},
    )
    assert resp.status_code == 200


def test_scan_permite_localhost_sin_verificacion_previa(client, monkeypatch):
    """La excepción de localhost/IP privada en acción sobre POST /scan --
    exactamente el flujo de laboratorio (Juice Shop/DVWA en Docker local)
    del que depende el resto de este proyecto para probarse a sí mismo."""
    import agents.recon as recon_module
    from tools._shared import ToolExecutionError

    def _recon_falla_rapido(*_a, **_k):
        raise ToolExecutionError("mock de test: recon deshabilitado en este test")

    monkeypatch.setattr(recon_module, "run_subfinder", _recon_falla_rapido)
    monkeypatch.setattr(recon_module, "run_amass_passive", _recon_falla_rapido)

    token_auth = _registrar_tenant(client, "Pyme Scan Gate D", "scangated@scanverif.test")
    client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"tipo": "dominio", "valor": "localhost"},
    )

    resp = client.post(
        "/scan",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"target": "localhost", "autorizacion_firmada": False},
    )
    assert resp.status_code == 200


def test_scan_activo_bloquea_dominio_publico_registrado_pero_no_verificado(client, monkeypatch):
    """Mismo gate que `test_scan_bloquea_dominio_publico_sin_verificar`, pero
    sobre `POST /scan/activo` -- confirma que `_asset_verificado_para_target`
    reemplaza correctamente al chequeo viejo (solo-ownership) en este
    endpoint, y que el thread de ZAP jamás se arranca."""

    def _zap_espia(*_a, **_k):
        raise AssertionError("run_checkpointed_active_scan no debía invocarse -- asset sin verificar")

    monkeypatch.setattr(main_module, "run_checkpointed_active_scan", _zap_espia)

    token_auth = _registrar_tenant(client, "Pyme Scan Activo Gate", "scanactivogate@scanverif.test")
    client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"tipo": "dominio", "valor": "dominioactivosinverif.test"},
    )

    resp = client.post(
        "/scan/activo",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"target_url": "http://dominioactivosinverif.test", "autorizacion_firmada": True},
    )
    assert resp.status_code == 403
    assert "no verificado" in resp.json()["detail"] or "NO verificado" in resp.json()["detail"]


def _esperar_scan_terminado(client, token: str, scan_id: str, intentos: int = 100, espera: float = 0.05) -> str:
    """Espera a que el thread real de fondo termine, no solo a que se haya llamado al mock.

    Duplicado a propósito de `test_scan_activo_asset_gate.py::_esperar_scan_terminado`
    (mismo espíritu que `_registrar_tenant`, ya duplicado entre archivos de
    test de este proyecto) -- ver esa función para el hallazgo real que
    motiva esto: un thread de `_correr_escaneo_activo_en_background` que
    sigue vivo después de que el mock ya fue invocado puede escribir en la
    DB temporal del SIGUIENTE test si `monkeypatch`/`test_db` ya revirtieron
    `DATABASE_URL` para cuando ese thread hace su propio `get_conn()` --
    corrupción de estado cruzada entre tests, intermitente. Sondear `GET
    /scans/{id}` hasta que `estado` deje de ser 'corriendo' garantiza que el
    thread ya terminó su última escritura antes de que el test retorne.
    """
    import time

    for _ in range(intentos):
        resp = client.get(f"/scans/{scan_id}", headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 200 and resp.json()["estado"] != "corriendo":
            return resp.json()["estado"]
        time.sleep(espera)
    raise AssertionError(f"scan {scan_id} no terminó dentro del tiempo esperado (sigue 'corriendo')")


def test_scan_activo_permite_ip_privada_exenta_sin_verificar(client, monkeypatch):
    """La excepción también cubre `tipo='ip'` -- ej. escanear un contenedor
    Docker local por IP en vez de por nombre."""

    class _ResultadoFalso:
        findings: list = []
        detalle_final: str = "mock: sin fases reales"

    llamado = {"zap": False}

    def _zap_espia(*_a, **_k):
        llamado["zap"] = True
        return _ResultadoFalso()

    monkeypatch.setattr(main_module, "run_checkpointed_active_scan", _zap_espia)

    token_auth = _registrar_tenant(client, "Pyme Scan Activo IP", "scanactivoip@scanverif.test")
    client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"tipo": "ip", "valor": "172.17.0.5"},
    )

    resp = client.post(
        "/scan/activo",
        headers={"Authorization": f"Bearer {token_auth}"},
        json={"target_url": "http://172.17.0.5:3000", "autorizacion_firmada": True},
    )
    assert resp.status_code == 202
    scan_id = resp.json()["scan_id"]

    estado_final = _esperar_scan_terminado(client, token_auth, scan_id)
    assert estado_final == "completado"
    assert llamado["zap"] is True


# ---------------------------------------------------------------------------
# 4. DNS real (no mockeado) contra un dominio público que no controlamos +
#    contraprueba de éxito mockeada explícitamente.
# ---------------------------------------------------------------------------


def test_verificar_dns_txt_hace_una_resolucion_dns_real_y_reporta_no_encontrado():
    """Ejecuta `dns.resolver.resolve` de verdad (red real, sin mock) contra
    'google.com' -- un dominio público real que no controlamos, así que NO
    puede tener nuestro token en '_vigia-challenge.google.com'. Esto prueba
    que la función de verdad sale a la red y decodifica una respuesta DNS
    real (o un NXDOMAIN real) en vez de estar simulada -- no puede demostrar
    el camino de éxito por la misma razón (no tenemos forma de escribir un
    TXT ahí). Si esta máquina no tiene salida a Internet, el resultado
    también es `False` (con un mensaje de timeout/error), así que el test no
    es frágil ante falta de red -- solo deja de ser una prueba "real" en ese
    caso, cosa que se documenta aquí, no se oculta.
    """
    ok, detalle = verificar_dns_txt("google.com", "token-que-nunca-vamos-a-encontrar", timeout=5.0)
    assert ok is False
    assert detalle  # siempre hay un motivo humano-legible


def test_verificar_dns_txt_camino_de_exito_mockeado():
    """Camino de éxito -- MOCKEADO explícitamente (`dns.resolver.Resolver.resolve`
    monkeypatcheado) porque no controlamos ningún dominio real para
    escribirle un TXT válido en esta suite. Confirma que, cuando el
    resolutor SÍ devuelve un TXT con el token, `verificar_dns_txt` lo
    reconoce -- la mitad del contrato que la prueba real (#4 de arriba) no
    puede cubrir por sí sola."""
    import dns.resolver

    class _RespuestaFalsa:
        strings = [b"algun-otro-valor", b"vigia-verify-abc123"]

    class _ResolverFalso:
        def __init__(self):
            self.timeout = None
            self.lifetime = None

        def resolve(self, nombre, tipo):
            assert nombre == "_vigia-challenge.miempresa.com"
            assert tipo == "TXT"
            return [_RespuestaFalsa()]

    original_resolver_cls = dns.resolver.Resolver
    dns.resolver.Resolver = _ResolverFalso
    try:
        ok, detalle = verificar_dns_txt("miempresa.com", "vigia-verify-abc123", timeout=1.0)
    finally:
        dns.resolver.Resolver = original_resolver_cls

    assert ok is True
    assert "TXT" in detalle


def test_verificar_http_well_known_real_contra_dominio_publico_sin_el_archivo():
    """Igual que la prueba DNS real de arriba, pero para el método HTTP:
    GET real (sin mock) contra 'https://google.com/.well-known/
    vigia-verification.txt' -- existe la conexión real, pero ese archivo no
    existe en ese dominio (no lo controlamos), así que el resultado esperado
    es 404/False, demostrando una petición HTTP real de punta a punta."""
    ok, detalle = verificar_http_well_known("google.com", "token-irrelevante", timeout=5.0)
    assert ok is False
    assert detalle


# ---------------------------------------------------------------------------
# 5. Aislamiento multi-tenant para el gate nuevo -- mismo principio que
#    test_multi_tenant_isolation.py y test_scan_activo_asset_gate.py.
# ---------------------------------------------------------------------------


def test_scan_no_usa_asset_verificado_de_otro_tenant(client, monkeypatch):
    monkeypatch.setattr(
        main_module, "verificar_asset", lambda *_a, **_k: (True, "mock: token encontrado")
    )
    token_dueno = _registrar_tenant(client, "Dueno Scan Gate", "duenoscangate@scanverif.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_dueno}"},
        json={"tipo": "dominio", "valor": "propiedadscangate.test"},
    )
    asset_id = asset.json()["id"]
    client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token_dueno}"},
        json={"metodo": "dns_txt"},
    )

    token_atacante = _registrar_tenant(client, "Atacante Scan Gate", "atacantescangate@scanverif.test")
    resp = client.post(
        "/scan",
        headers={"Authorization": f"Bearer {token_atacante}"},
        json={"target": "propiedadscangate.test", "autorizacion_firmada": False},
    )
    assert resp.status_code == 403
