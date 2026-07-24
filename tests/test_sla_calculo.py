"""Fase 2a (openspec/changes/fase2a-ciclo-vida-hallazgo) -- cálculo de `sla_due_at`.

Cubre: SLA default por severidad, ausencia de plazo para low/info, y override
vía `tenant_sla_config` (config propia del tenant que anula el default).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from api.main import DEFAULT_SLA_DIAS, _calcular_sla_due_at
from db.connection import get_conn


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123") -> str:
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    resp_me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    return resp_me.json()["tenant_id"]


def test_severidad_critical_recibe_sla_default_de_7_dias(client):
    tenant_id = _registrar_tenant(client, "Tenant SLA Critical", "slacritical@sla.test")
    now = datetime.now(timezone.utc).isoformat()

    conn = get_conn()
    try:
        sla = _calcular_sla_due_at(conn, tenant_id, "critical", now)
    finally:
        conn.close()

    assert sla is not None
    esperado = datetime.fromisoformat(now) + timedelta(days=DEFAULT_SLA_DIAS["critical"])
    assert datetime.fromisoformat(sla) == esperado


def test_severidad_low_e_info_no_tienen_plazo(client):
    tenant_id = _registrar_tenant(client, "Tenant SLA Low", "slalow@sla.test")
    now = datetime.now(timezone.utc).isoformat()

    conn = get_conn()
    try:
        assert _calcular_sla_due_at(conn, tenant_id, "low", now) is None
        assert _calcular_sla_due_at(conn, tenant_id, "info", now) is None
    finally:
        conn.close()


def test_config_de_sla_propia_del_tenant_anula_el_default(client):
    tenant_id = _registrar_tenant(client, "Tenant SLA Custom", "slacustom@sla.test")
    now = datetime.now(timezone.utc).isoformat()

    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO tenant_sla_config (tenant_id, dias_critical, dias_high, dias_medium, dias_low) "
            "VALUES (?, 7, 15, 90, NULL)",
            (tenant_id,),
        )
        conn.commit()

        sla_high = _calcular_sla_due_at(conn, tenant_id, "high", now)
        sla_low = _calcular_sla_due_at(conn, tenant_id, "low", now)
    finally:
        conn.close()

    esperado = datetime.fromisoformat(now) + timedelta(days=15)
    assert datetime.fromisoformat(sla_high) == esperado
    assert sla_low is None


def test_tenant_sin_config_propia_usa_default_para_medium(client):
    tenant_id = _registrar_tenant(client, "Tenant SLA Medium", "slamedium@sla.test")
    now = datetime.now(timezone.utc).isoformat()

    conn = get_conn()
    try:
        sla = _calcular_sla_due_at(conn, tenant_id, "medium", now)
    finally:
        conn.close()

    esperado = datetime.fromisoformat(now) + timedelta(days=DEFAULT_SLA_DIAS["medium"])
    assert datetime.fromisoformat(sla) == esperado
