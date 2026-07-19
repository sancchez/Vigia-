"""`POST /scan/activo` -- el target debe corresponder a un asset del tenant.

Ver `agents/revision_ia.py` (hallazgo real de esta sesión, documentado en
HANDOFF.md item transversal "Usar Claude para construir mejores
herramientas"): este endpoint arranca `run_zap_active_scan` directo en un
thread, sin pasar por `orchestrator/graph.py::gate_autorizacion` -- antes
de este fix, el único control era el booleano `autorizacion_firmada` que
el propio cliente manda en el body, sin ninguna verificación de que
`target_url` fuera algo que el tenant hubiera registrado de verdad vía
`POST /assets`. Cualquier tenant autenticado podía escanear activamente
CUALQUIER URL con solo marcar `autorizacion_firmada: true`.

El fix (`api/main.py::_tenant_tiene_asset_para_target`) es una función
determinista y plana, en el mismo espíritu que `gate_autorizacion` (Python
puro, sin LLM), pero deliberadamente NO comparte código con ella: contesta
una pregunta distinta ("¿este target es un asset de este tenant?" en vez
de "¿el booleano de autorización es True?"). Dos niveles de prueba, mismo
patrón que `test_gate_autorizacion.py`:

1. Unidad pura sobre `_tenant_tiene_asset_para_target()` -- sin HTTP, sin
   threads, sin red.
2. Integración sobre `POST /scan/activo` real vía `TestClient` -- confirma
   que el endpoint completo rechaza con 403 antes de arrancar el thread de
   `run_zap_active_scan` (espiado con monkeypatch para detectar si se
   invocó), y que un tenant con el asset registrado sí puede avanzar.
"""

from __future__ import annotations

import uuid

import api.main as main_module
from db.connection import get_conn


# ---------------------------------------------------------------------------
# 1. Unidad pura -- _tenant_tiene_asset_para_target() como función Python plana.
# ---------------------------------------------------------------------------


def _crear_tenant_directo(conn, tenant_id: str) -> None:
    """Inserta la fila `tenants` mínima que exige la FK de `assets.tenant_id`.

    Los tests de unidad de esta sección siembran datos directo en la DB
    (sin pasar por `POST /auth/register`) porque solo les importa probar
    `_tenant_tiene_asset_para_target()`, no el flujo de registro completo.
    """
    conn.execute(
        "INSERT INTO tenants (id, slug, name) VALUES (?, ?, ?)",
        (tenant_id, tenant_id, f"Tenant de prueba {tenant_id}"),
    )


def _sembrar_asset(tenant_id: str, tipo: str, valor: str) -> None:
    conn = get_conn()
    try:
        if not conn.execute("SELECT 1 FROM tenants WHERE id = ?", (tenant_id,)).fetchone():
            _crear_tenant_directo(conn, tenant_id)
        conn.execute(
            "INSERT INTO assets (id, tenant_id, tipo, valor) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), tenant_id, tipo, valor),
        )
        conn.commit()
    finally:
        conn.close()


def test_rechaza_target_que_el_tenant_nunca_registro(test_db):
    tenant_id = str(uuid.uuid4())
    _sembrar_asset(tenant_id, "dominio", "miempresa.com")

    conn = get_conn()
    try:
        assert (
            main_module._tenant_tiene_asset_para_target(
                conn, tenant_id, "http://otraempresa-cualquiera.com"
            )
            is False
        )
    finally:
        conn.close()


def test_permite_apex_exacto_registrado(test_db):
    tenant_id = str(uuid.uuid4())
    _sembrar_asset(tenant_id, "dominio", "miempresa.com")

    conn = get_conn()
    try:
        assert (
            main_module._tenant_tiene_asset_para_target(conn, tenant_id, "http://miempresa.com")
            is True
        )
    finally:
        conn.close()


def test_permite_subdominio_del_apex_registrado(test_db):
    """Un asset 'miempresa.com' también autoriza 'app.miempresa.com' --
    mismo criterio de dominio registrable que usa el listener de CertStream
    (`tools.antisuplantacion.registrable_domain`)."""
    tenant_id = str(uuid.uuid4())
    _sembrar_asset(tenant_id, "dominio", "miempresa.com")

    conn = get_conn()
    try:
        assert (
            main_module._tenant_tiene_asset_para_target(
                conn, tenant_id, "https://app.miempresa.com/login"
            )
            is True
        )
    finally:
        conn.close()


def test_permite_ip_registrada_por_coincidencia_exacta(test_db):
    tenant_id = str(uuid.uuid4())
    _sembrar_asset(tenant_id, "ip", "203.0.113.7")

    conn = get_conn()
    try:
        assert (
            main_module._tenant_tiene_asset_para_target(conn, tenant_id, "http://203.0.113.7:8080")
            is True
        )
        assert (
            main_module._tenant_tiene_asset_para_target(conn, tenant_id, "http://203.0.113.99")
            is False
        )
    finally:
        conn.close()


def test_no_confunde_activos_de_otro_tenant(test_db):
    """El asset existe de verdad, pero pertenece a OTRO tenant -- sigue sin
    autorizar. Es el mismo tipo de bug que `test_multi_tenant_isolation.py`
    ya cubre para `/findings` y `/reports/cumplimiento`."""
    tenant_dueno = str(uuid.uuid4())
    tenant_atacante = str(uuid.uuid4())
    _sembrar_asset(tenant_dueno, "dominio", "victima.com")

    conn = get_conn()
    try:
        assert (
            main_module._tenant_tiene_asset_para_target(
                conn, tenant_atacante, "http://victima.com"
            )
            is False
        )
    finally:
        conn.close()


def test_asset_inactivo_no_autoriza(test_db):
    tenant_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        _crear_tenant_directo(conn, tenant_id)
        conn.execute(
            "INSERT INTO assets (id, tenant_id, tipo, valor, is_active) VALUES (?, ?, 'dominio', ?, 0)",
            (str(uuid.uuid4()), tenant_id, "miempresa.com"),
        )
        conn.commit()
        assert (
            main_module._tenant_tiene_asset_para_target(conn, tenant_id, "http://miempresa.com")
            is False
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Integración -- POST /scan/activo real jamás arranca el thread de ZAP
#    cuando el target no es un asset del tenant, aunque autorizacion_firmada
#    sea true.
# ---------------------------------------------------------------------------


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123"):
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _esperar_scan_terminado(client, token: str, scan_id: str, intentos: int = 100, espera: float = 0.05) -> str:
    """Espera a que el thread de `_correr_escaneo_activo_en_background` termine de verdad.

    Hallazgo real de esta sesión (Corrida 16): esperar solo a que se haya
    LLAMADO al mock de `run_zap_active_scan` (el patrón viejo de este
    archivo) no es suficiente -- el thread real sigue vivo después de eso
    (todavía tiene que hacer `get_conn()` + `UPDATE scans` + insertar
    findings), y si el test termina y `monkeypatch`/`test_db` revierten
    `DATABASE_URL` ANTES de que ese `get_conn()` interno se ejecute, el
    thread zombie escribe en la base de datos temporal del SIGUIENTE test
    que ya arrancó -- corrupción de estado cruzada entre tests, intermitente
    según el scheduling exacto del SO (confirmado en vivo: la suite completa
    falló de forma no determinista en tests SIN relación aparente hasta
    diagnosticar esto). Sondear `GET /scans/{id}` hasta que `estado` deje de
    ser 'corriendo' SÍ garantiza que el thread ya hizo su última escritura
    a la DB, porque esa escritura es precisamente la que cambia `estado`.
    """
    import time

    for _ in range(intentos):
        resp = client.get(f"/scans/{scan_id}", headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 200 and resp.json()["estado"] != "corriendo":
            return resp.json()["estado"]
        time.sleep(espera)
    raise AssertionError(f"scan {scan_id} no terminó dentro del tiempo esperado (sigue 'corriendo')")


def test_scan_activo_rechaza_target_sin_asset_registrado(client, monkeypatch):
    llamado = {"zap": False}

    def _zap_espia(*_args, **_kwargs):
        llamado["zap"] = True
        raise AssertionError(
            "run_checkpointed_active_scan se invocó contra un target que el "
            "tenant nunca registró como asset -- exactamente el gap real que "
            "agents/revision_ia.py encontró en esta sesión."
        )

    monkeypatch.setattr(main_module, "run_checkpointed_active_scan", _zap_espia)

    token = _registrar_tenant(client, "Pyme Sin Assets", "sinassets@scanactivo.test")

    resp = client.post(
        "/scan/activo",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "target_url": "http://dominio-jamas-registrado.test",
            "autorizacion_firmada": True,
        },
    )

    assert resp.status_code == 403
    assert "activo registrado" in resp.json()["detail"]
    assert llamado["zap"] is False


def test_scan_activo_permite_target_con_asset_registrado(client, monkeypatch):
    """Contraprueba necesaria (mismo principio que
    test_gate_autorizacion.py::test_grafo_real_permite_escaneo_con_autorizacion_true):
    el nuevo check no debe bloquear de más un target legítimo.

    Actualizado esta sesión (verificación de propiedad de dominio, ver
    `tools/asset_verification.py`): 'midominiolegitimo.test' es un dominio
    público (no localhost/IP privada), así que además de estar registrado
    como asset ahora también necesita estar VERIFICADO -- registrar un
    dominio ya no es suficiente por sí solo para autorizar un escaneo, que es
    exactamente el gap que esta sesión cierra. Se mockea la comprobación
    DNS/HTTP real (`main_module.verificar_asset`) para simular un TXT válido
    sin depender de que 'midominiolegitimo.test' resuelva de verdad -- el
    endpoint `POST /assets/{id}/verify` en sí (parseo de respuesta, updates
    de DB) sí corre real."""
    llamado = {"zap": False}

    class _ResultadoFalso:
        findings: list = []
        detalle_final: str = "mock: sin fases reales"

    def _zap_espia(*_args, **_kwargs):
        llamado["zap"] = True
        return _ResultadoFalso()

    monkeypatch.setattr(main_module, "run_checkpointed_active_scan", _zap_espia)
    monkeypatch.setattr(
        main_module, "verificar_asset", lambda *_a, **_k: (True, "mock: token encontrado")
    )

    token = _registrar_tenant(client, "Pyme Con Asset", "conasset@scanactivo.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token}"},
        json={"tipo": "dominio", "valor": "midominiolegitimo.test"},
    )
    assert asset.status_code == 200
    asset_id = asset.json()["id"]

    verificacion = client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token}"},
        json={"metodo": "dns_txt"},
    )
    assert verificacion.status_code == 200
    assert verificacion.json()["verificado"] is True

    resp = client.post(
        "/scan/activo",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "target_url": "http://midominiolegitimo.test",
            "autorizacion_firmada": True,
        },
    )

    assert resp.status_code == 202
    scan_id = resp.json()["scan_id"]
    assert scan_id

    # Espera a que el thread real termine del todo (no solo a que se haya
    # llamado al mock) -- ver docstring de _esperar_scan_terminado, evita
    # que el thread zombie escriba en la DB temporal del siguiente test.
    estado_final = _esperar_scan_terminado(client, token, scan_id)
    assert estado_final == "completado"
    assert llamado["zap"] is True


def test_scan_activo_rechaza_target_de_otro_tenant(client, monkeypatch):
    """El dominio SÍ es un asset real -- pero de otro tenant. Mismo
    principio de aislamiento multi-tenant que test_multi_tenant_isolation.py."""

    def _zap_espia(*_args, **_kwargs):
        raise AssertionError("no debía llegar a invocar el escaneo real")

    monkeypatch.setattr(main_module, "run_checkpointed_active_scan", _zap_espia)

    token_dueno = _registrar_tenant(client, "Dueno Real", "dueno@scanactivo.test")
    client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_dueno}"},
        json={"tipo": "dominio", "valor": "propiedadajena.test"},
    )

    token_atacante = _registrar_tenant(client, "Otro Tenant", "atacante@scanactivo.test")
    resp = client.post(
        "/scan/activo",
        headers={"Authorization": f"Bearer {token_atacante}"},
        json={
            "target_url": "http://propiedadajena.test",
            "autorizacion_firmada": True,
        },
    )

    assert resp.status_code == 403
