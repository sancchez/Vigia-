"""Ramificación activo/pasivo del scheduler recurrente (Fase 6a,
openspec/changes/fase6-endurecimiento-produccion) -- `api/scheduler.py`.

Ver design.md, Decisión 6a.3: `autorizacion_activa` (columna persistida en
`assets`, NUNCA un booleano de request) decide si el ciclo recurrente
dispara Escaneo Activo real (ZAP, mockeado aquí) o sigue con recon pasivo
como antes de esta sesión. `scan_paused`/`scan_frequency_hours` (Decisión
6a.4) pueden saltar un activo del ciclo antes siquiera de llegar a esa
decisión.

También cubre `PUT /assets/{id}/schedule` (6a.4.1): scoping por tenant.
"""

from __future__ import annotations

import uuid

import api.scheduler as scheduler_module
from db.connection import get_conn


def _crear_tenant(conn, tenant_id: str) -> None:
    conn.execute(
        "INSERT INTO tenants (id, slug, name) VALUES (?, ?, ?)",
        (tenant_id, tenant_id, f"Tenant {tenant_id}"),
    )


def _crear_asset(
    tenant_id: str,
    valor: str,
    *,
    autorizacion_activa: int = 0,
    verificado: int = 1,
    scan_paused: int = 0,
    scan_frequency_hours=None,
) -> str:
    asset_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        if not conn.execute("SELECT 1 FROM tenants WHERE id = ?", (tenant_id,)).fetchone():
            _crear_tenant(conn, tenant_id)
        conn.execute(
            """
            INSERT INTO assets
                (id, tenant_id, tipo, valor, verificado, autorizacion_activa, scan_paused, scan_frequency_hours)
            VALUES (?, ?, 'dominio', ?, ?, ?, ?, ?)
            """,
            (asset_id, tenant_id, valor, verificado, autorizacion_activa, scan_paused, scan_frequency_hours),
        )
        conn.commit()
    finally:
        conn.close()
    return asset_id


class _ResultadoZapFalso:
    findings: list = []
    detalle_final: str = "mock: sin fases reales"


def test_autorizacion_activa_dispara_escaneo_activo_real(test_db, monkeypatch):
    llamado = {"zap": False, "grafo": False}

    def _zap_espia(*_args, **_kwargs):
        llamado["zap"] = True
        return _ResultadoZapFalso()

    def _grafo_espia(*_args, **_kwargs):
        llamado["grafo"] = True
        return {"reporte_final": None, "trace_log": []}

    monkeypatch.setattr(scheduler_module, "run_checkpointed_active_scan", _zap_espia)
    monkeypatch.setattr(scheduler_module, "_get_graph", lambda: type("G", (), {"invoke": staticmethod(_grafo_espia)})())

    tenant_id = str(uuid.uuid4())
    _crear_asset(tenant_id, "activo.test", autorizacion_activa=1, verificado=1)

    procesados = scheduler_module.run_scan_cycle_once()

    assert procesados == 1
    assert llamado["zap"] is True
    assert llamado["grafo"] is False

    conn = get_conn()
    try:
        fila = dict(conn.execute("SELECT * FROM scans WHERE tenant_id = ?", (tenant_id,)).fetchone())
    finally:
        conn.close()
    assert fila["autorizacion_firmada"] == 1
    assert fila["estado"] == "completado"


def test_sin_autorizacion_activa_corre_pasivo(test_db, monkeypatch):
    llamado = {"zap": False, "grafo": False}

    def _zap_espia(*_args, **_kwargs):
        llamado["zap"] = True
        raise AssertionError("no debía invocar Escaneo Activo sin autorizacion_activa=1")

    # Fase 3: el grafo lleva checkpointer, así que el scheduler invoca con
    # `config={"configurable": {"thread_id": scan_id}}`. El espía lo captura
    # para verificar que el thread_id sea el scan_id real de esta corrida
    # (si fuera otro, dos scans distintos compartirían hilo de checkpoint).
    def _grafo_espia(estado, config=None):
        llamado["grafo"] = True
        llamado["thread_id"] = (config or {}).get("configurable", {}).get("thread_id")
        return {**estado, "reporte_final": "recon pasivo ok", "trace_log": []}

    monkeypatch.setattr(scheduler_module, "run_checkpointed_active_scan", _zap_espia)
    monkeypatch.setattr(scheduler_module, "_get_graph", lambda: type("G", (), {"invoke": staticmethod(_grafo_espia)})())

    tenant_id = str(uuid.uuid4())
    _crear_asset(tenant_id, "pasivo.test", autorizacion_activa=0, verificado=1)

    procesados = scheduler_module.run_scan_cycle_once()

    assert procesados == 1
    assert llamado["grafo"] is True
    assert llamado["zap"] is False

    conn = get_conn()
    try:
        fila = dict(conn.execute("SELECT * FROM scans WHERE tenant_id = ?", (tenant_id,)).fetchone())
    finally:
        conn.close()
    assert fila["autorizacion_firmada"] == 0
    # El hilo de checkpoint es el scan_id que quedó persistido, no otro.
    assert llamado["thread_id"] == fila["id"]


def test_no_verificado_se_omite_del_ciclo(test_db, monkeypatch):
    def _zap_espia(*_args, **_kwargs):
        raise AssertionError("no debía correr nada para un activo no verificado")

    def _invoke_espia(*_args, **_kwargs):
        raise AssertionError("no debía invocar el grafo para un activo no verificado")

    monkeypatch.setattr(scheduler_module, "run_checkpointed_active_scan", _zap_espia)
    monkeypatch.setattr(
        scheduler_module, "_get_graph", lambda: type("G", (), {"invoke": staticmethod(_invoke_espia)})()
    )

    tenant_id = str(uuid.uuid4())
    _crear_asset(tenant_id, "sinverificar.test", autorizacion_activa=1, verificado=0)

    procesados = scheduler_module.run_scan_cycle_once()

    assert procesados == 0
    conn = get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM scans WHERE tenant_id = ?", (tenant_id,)).fetchone()
    finally:
        conn.close()
    assert dict(n)["n"] == 0


def test_pausado_se_omite_del_ciclo(test_db, monkeypatch):
    def _zap_espia(*_args, **_kwargs):
        raise AssertionError("no debía correr nada para un activo pausado")

    monkeypatch.setattr(scheduler_module, "run_checkpointed_active_scan", _zap_espia)

    tenant_id = str(uuid.uuid4())
    _crear_asset(tenant_id, "pausado.test", autorizacion_activa=1, verificado=1, scan_paused=1)

    procesados = scheduler_module.run_scan_cycle_once()

    assert procesados == 0


def test_frecuencia_no_cumplida_se_omite(test_db, monkeypatch):
    """Un activo con `scan_frequency_hours` alto y un scan reciente no debe
    volver a correr todavía."""

    def _zap_espia(*_args, **_kwargs):
        raise AssertionError("no debía correr -- aún no cumple su frecuencia")

    monkeypatch.setattr(scheduler_module, "run_checkpointed_active_scan", _zap_espia)

    tenant_id = str(uuid.uuid4())
    asset_id = _crear_asset(
        tenant_id, "frecuencia.test", autorizacion_activa=1, verificado=1, scan_frequency_hours=999999
    )
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO scans (id, tenant_id, asset_id, target, estado) VALUES (?, ?, ?, ?, 'completado')",
            (str(uuid.uuid4()), tenant_id, asset_id, "frecuencia.test"),
        )
        conn.commit()
    finally:
        conn.close()

    procesados = scheduler_module.run_scan_cycle_once()

    assert procesados == 0


# ---------------------------------------------------------------------------
# PUT /assets/{id}/schedule -- scoping por tenant (6a.5.4)
# ---------------------------------------------------------------------------


def _registrar_tenant_http(client, nombre: str, email: str) -> str:
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": "claveSegura123"},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def test_put_schedule_actualiza_frecuencia_y_pausa(client):
    token = _registrar_tenant_http(client, "Pyme Programacion", "programacion@fase6a.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token}"},
        json={"tipo": "dominio", "valor": "programado.test"},
    ).json()

    resp = client.put(
        f"/assets/{asset['id']}/schedule",
        headers={"Authorization": f"Bearer {token}"},
        json={"scan_frequency_hours": 12, "scan_paused": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scan_frequency_hours"] == 12
    assert body["scan_paused"] is True


def test_put_schedule_404_cross_tenant(client):
    token_dueno = _registrar_tenant_http(client, "Dueno Programacion", "dueno@fase6a.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token_dueno}"},
        json={"tipo": "dominio", "valor": "ajeno.test"},
    ).json()

    token_atacante = _registrar_tenant_http(client, "Atacante Programacion", "atacante@fase6a.test")
    resp = client.put(
        f"/assets/{asset['id']}/schedule",
        headers={"Authorization": f"Bearer {token_atacante}"},
        json={"scan_paused": True},
    )
    assert resp.status_code == 404
