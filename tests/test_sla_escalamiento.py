"""Fase 5 (openspec/changes/fase5-notificaciones-alertas): escalamiento de
SLA (`api/scheduler.py::run_sla_escalation_once`).

Cubre: umbral 75% dispara una vez, vencido dispara una vez (independiente
del de 75%), y un finding cerrado antes del umbral no dispara nada.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import api.notificaciones as notif_module
from api.scheduler import run_sla_escalation_once
from db.connection import get_conn


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123") -> tuple[str, str]:
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    resp_me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    return token, resp_me.json()["tenant_id"]


def _crear_regla(conn, tenant_id: str) -> None:
    conn.execute(
        "INSERT INTO notification_rules (id, tenant_id, evento, canal, destino, is_active) "
        "VALUES (?, ?, 'sla_escalamiento', 'email', 'ops@x.co', 1)",
        (str(uuid.uuid4()), tenant_id),
    )
    conn.commit()


def _crear_scan(conn, tenant_id: str) -> str:
    scan_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado)
        VALUES (?, ?, 'target.test', 0, 'completado')
        """,
        (scan_id, tenant_id),
    )
    conn.commit()
    return scan_id


def _crear_finding(
    conn, tenant_id: str, scan_id: str, created_at: str, sla_due_at: str, estado: str = "descubierto"
) -> str:
    finding_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, created_at, sla_due_at, estado)
        VALUES (?, ?, ?, 'xss', 'high', '/ep', ?, ?, ?)
        """,
        (finding_id, scan_id, tenant_id, created_at, sla_due_at, estado),
    )
    conn.commit()
    return finding_id


def test_finding_al_75_por_ciento_del_plazo_dispara_una_vez(client, monkeypatch):
    monkeypatch.setattr(notif_module, "_enviar_email_smtp", lambda *_a, **_k: True)
    _, tenant_id = _registrar_tenant(client, "Tenant SLA 75", "sla75@sla.test")

    conn = get_conn()
    try:
        _crear_regla(conn, tenant_id)
        scan_id = _crear_scan(conn, tenant_id)

        # Plazo de 10 días, 8 transcurridos (80% > 75%), sla_due_at futuro.
        ahora = datetime.now(timezone.utc)
        creado = ahora - timedelta(days=8)
        vence = creado + timedelta(days=10)
        finding_id = _crear_finding(conn, tenant_id, scan_id, creado.isoformat(), vence.isoformat())

        procesados = run_sla_escalation_once()
        assert procesados == 1

        fila = conn.execute(
            "SELECT * FROM notifications WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        assert fila is not None
        assert fila["evento"] == f"sla_escalamiento:75pct:{finding_id}"

        # Segunda corrida -- mismo umbral, no debe re-disparar (idempotencia).
        procesados_2 = run_sla_escalation_once()
        assert procesados_2 == 1  # sigue "cruzando" el umbral, pero no reenvía
        filas = conn.execute(
            "SELECT * FROM notifications WHERE tenant_id = ?", (tenant_id,)
        ).fetchall()
        assert len(filas) == 1
    finally:
        conn.close()


def test_finding_vencido_dispara_umbral_vencido_una_vez(client, monkeypatch):
    monkeypatch.setattr(notif_module, "_enviar_email_smtp", lambda *_a, **_k: True)
    _, tenant_id = _registrar_tenant(client, "Tenant SLA Vencido", "slavencido@sla.test")

    conn = get_conn()
    try:
        _crear_regla(conn, tenant_id)
        scan_id = _crear_scan(conn, tenant_id)

        ahora = datetime.now(timezone.utc)
        creado = ahora - timedelta(days=10)
        vence = ahora - timedelta(days=1)  # ya venció
        finding_id = _crear_finding(conn, tenant_id, scan_id, creado.isoformat(), vence.isoformat())

        procesados = run_sla_escalation_once()
        assert procesados == 1

        fila = conn.execute(
            "SELECT * FROM notifications WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        assert fila is not None
        assert fila["evento"] == f"sla_escalamiento:vencido:{finding_id}"
    finally:
        conn.close()


def test_finding_cerrado_antes_del_umbral_no_dispara_nada(client, monkeypatch):
    llamado = {"n": 0}

    def _espia(*_a, **_k):
        llamado["n"] += 1
        return True

    monkeypatch.setattr(notif_module, "_enviar_email_smtp", _espia)
    _, tenant_id = _registrar_tenant(client, "Tenant SLA Cerrado", "slacerrado@sla.test")

    conn = get_conn()
    try:
        _crear_regla(conn, tenant_id)
        scan_id = _crear_scan(conn, tenant_id)

        ahora = datetime.now(timezone.utc)
        creado = ahora - timedelta(days=10)
        vence = ahora - timedelta(days=1)  # habría vencido, pero está cerrado
        _crear_finding(conn, tenant_id, scan_id, creado.isoformat(), vence.isoformat(), estado="cerrado")

        procesados = run_sla_escalation_once()
        assert procesados == 0
        assert llamado["n"] == 0

        fila = conn.execute(
            "SELECT * FROM notifications WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        assert fila is None
    finally:
        conn.close()


def test_finding_temprano_en_el_plazo_no_dispara(client, monkeypatch):
    monkeypatch.setattr(notif_module, "_enviar_email_smtp", lambda *_a, **_k: True)
    _, tenant_id = _registrar_tenant(client, "Tenant SLA Temprano", "slatemprano@sla.test")

    conn = get_conn()
    try:
        _crear_regla(conn, tenant_id)
        scan_id = _crear_scan(conn, tenant_id)

        ahora = datetime.now(timezone.utc)
        creado = ahora - timedelta(days=1)
        vence = creado + timedelta(days=10)  # 10% transcurrido, muy lejos del 75%
        _crear_finding(conn, tenant_id, scan_id, creado.isoformat(), vence.isoformat())

        procesados = run_sla_escalation_once()
        assert procesados == 0
    finally:
        conn.close()
