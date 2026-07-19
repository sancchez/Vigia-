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


def test_scan_activo_rechaza_target_sin_asset_registrado(client, monkeypatch):
    llamado = {"zap": False}

    def _zap_espia(*_args, **_kwargs):
        llamado["zap"] = True
        raise AssertionError(
            "run_zap_active_scan se invocó contra un target que el tenant "
            "nunca registró como asset -- exactamente el gap real que "
            "agents/revision_ia.py encontró en esta sesión."
        )

    monkeypatch.setattr(main_module, "run_zap_active_scan", _zap_espia)

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
    el nuevo check no debe bloquear de más un target legítimo."""
    llamado = {"zap": False}

    class _ResultadoFalso:
        findings: list = []

    def _zap_espia(*_args, **_kwargs):
        llamado["zap"] = True
        return _ResultadoFalso()

    monkeypatch.setattr(main_module, "run_zap_active_scan", _zap_espia)

    token = _registrar_tenant(client, "Pyme Con Asset", "conasset@scanactivo.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token}"},
        json={"tipo": "dominio", "valor": "midominiolegitimo.test"},
    )
    assert asset.status_code == 200

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

    # El thread de fondo corre async -- puede no haber terminado todavía,
    # pero para este test lo único que importa es que SÍ se haya llamado
    # (a diferencia del caso rechazado, donde jamás se llama).
    import time

    for _ in range(50):
        if llamado["zap"]:
            break
        time.sleep(0.05)
    assert llamado["zap"] is True


def test_scan_activo_rechaza_target_de_otro_tenant(client, monkeypatch):
    """El dominio SÍ es un asset real -- pero de otro tenant. Mismo
    principio de aislamiento multi-tenant que test_multi_tenant_isolation.py."""

    def _zap_espia(*_args, **_kwargs):
        raise AssertionError("no debía llegar a invocar el escaneo real")

    monkeypatch.setattr(main_module, "run_zap_active_scan", _zap_espia)

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
