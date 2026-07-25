"""Fase 5 (openspec/changes/fase5-notificaciones-alertas): despacho de
notificaciones (`api/notificaciones.py`).

Cubre: envío regido por `notification_rules` (con/sin regla activa),
idempotencia por recurso, best-effort ante fallo/ausencia de SMTP, y el CRUD
de `/notification-rules` (200/404 cross-tenant/422 canal no soportado).
"""

from __future__ import annotations

import uuid

import api.notificaciones as notif_module
from api.notificaciones import enviar_notificacion
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


def _crear_regla(conn, tenant_id: str, evento: str, destino: str = "ops@x.co", activa: bool = True) -> None:
    conn.execute(
        "INSERT INTO notification_rules (id, tenant_id, evento, canal, destino, is_active) "
        "VALUES (?, ?, ?, 'email', ?, ?)",
        (str(uuid.uuid4()), tenant_id, evento, destino, int(activa)),
    )
    conn.commit()


def test_envio_con_regla_activa_registra_notificacion(client, monkeypatch):
    _, tenant_id = _registrar_tenant(client, "Tenant Notif", "notif1@notif.test")

    monkeypatch.setattr(notif_module, "_enviar_email_smtp", lambda *_a, **_k: True)

    conn = get_conn()
    try:
        _crear_regla(conn, tenant_id, "hallazgo_critico_nuevo", "ops@x.co")

        enviado = enviar_notificacion(
            conn, tenant_id, "hallazgo_critico_nuevo", "finding-1", "Asunto", "Cuerpo"
        )
        assert enviado is True

        fila = conn.execute(
            "SELECT * FROM notifications WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        assert fila is not None
        assert fila["enviado_en"] is not None
        assert fila["destino"] == "ops@x.co"
    finally:
        conn.close()


def test_sin_regla_activa_no_envia_ni_registra(client, monkeypatch):
    _, tenant_id = _registrar_tenant(client, "Tenant Notif Sin Regla", "notif2@notif.test")

    llamado = {"smtp": False}

    def _espia(*_a, **_k):
        llamado["smtp"] = True
        return True

    monkeypatch.setattr(notif_module, "_enviar_email_smtp", _espia)

    conn = get_conn()
    try:
        enviado = enviar_notificacion(
            conn, tenant_id, "hallazgo_critico_nuevo", "finding-2", "Asunto", "Cuerpo"
        )
        assert enviado is False
        assert llamado["smtp"] is False

        fila = conn.execute(
            "SELECT * FROM notifications WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        assert fila is None
    finally:
        conn.close()


def test_idempotencia_no_reenvia_el_mismo_recurso(client, monkeypatch):
    _, tenant_id = _registrar_tenant(client, "Tenant Notif Idem", "notif3@notif.test")

    llamadas = {"n": 0}

    def _espia(*_a, **_k):
        llamadas["n"] += 1
        return True

    monkeypatch.setattr(notif_module, "_enviar_email_smtp", _espia)

    conn = get_conn()
    try:
        _crear_regla(conn, tenant_id, "hallazgo_critico_nuevo", "ops@x.co")

        primero = enviar_notificacion(
            conn, tenant_id, "hallazgo_critico_nuevo", "finding-3", "Asunto", "Cuerpo"
        )
        segundo = enviar_notificacion(
            conn, tenant_id, "hallazgo_critico_nuevo", "finding-3", "Asunto", "Cuerpo"
        )
        assert primero is True
        assert segundo is False
        assert llamadas["n"] == 1

        filas = conn.execute(
            "SELECT * FROM notifications WHERE tenant_id = ?", (tenant_id,)
        ).fetchall()
        assert len(filas) == 1
    finally:
        conn.close()


def test_fallo_smtp_no_rompe_al_llamante_y_queda_enviado_en_nulo(client, monkeypatch):
    _, tenant_id = _registrar_tenant(client, "Tenant Notif Fallo", "notif4@notif.test")

    def _espia_falla(*_a, **_k):
        raise ConnectionRefusedError("SMTP no disponible")

    # `_enviar_email_smtp` ya atrapa toda excepción internamente y retorna
    # False -- monkeypatchearlo para lanzar simula justamente ese fallo real
    # sin salir de su propio try/except (queda dentro de notificaciones.py).
    def _enviar_falso(destino, asunto, cuerpo):
        try:
            _espia_falla()
        except Exception:
            return False
        return True

    monkeypatch.setattr(notif_module, "_enviar_email_smtp", _enviar_falso)

    conn = get_conn()
    try:
        _crear_regla(conn, tenant_id, "hallazgo_critico_nuevo", "ops@x.co")

        # No debe lanzar -- el llamante (persistencia de findings/scheduler)
        # sigue su curso normal.
        enviado = enviar_notificacion(
            conn, tenant_id, "hallazgo_critico_nuevo", "finding-4", "Asunto", "Cuerpo"
        )
        assert enviado is False

        fila = conn.execute(
            "SELECT * FROM notifications WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        assert fila is not None
        assert fila["enviado_en"] is None
    finally:
        conn.close()


def test_smtp_no_configurado_omite_limpio_sin_lanzar(client, monkeypatch):
    """Sin `VIGIA_SMTP_HOST` en el entorno, `_enviar_email_smtp` real (no
    mockeada) debe degradar en silencio -- ejercita el código real de
    `_config_smtp()`, no un mock."""
    monkeypatch.delenv("VIGIA_SMTP_HOST", raising=False)
    _, tenant_id = _registrar_tenant(client, "Tenant Notif SinSMTP", "notif5@notif.test")

    conn = get_conn()
    try:
        _crear_regla(conn, tenant_id, "hallazgo_critico_nuevo", "ops@x.co")

        enviado = enviar_notificacion(
            conn, tenant_id, "hallazgo_critico_nuevo", "finding-5", "Asunto", "Cuerpo"
        )
        assert enviado is False

        fila = conn.execute(
            "SELECT * FROM notifications WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        assert fila is not None
        assert fila["enviado_en"] is None
    finally:
        conn.close()


def test_dos_reglas_con_destinos_distintos_reciben_notificacion_independiente(client, monkeypatch):
    _, tenant_id = _registrar_tenant(client, "Tenant Notif Multi", "notif6@notif.test")
    monkeypatch.setattr(notif_module, "_enviar_email_smtp", lambda *_a, **_k: True)

    conn = get_conn()
    try:
        _crear_regla(conn, tenant_id, "hallazgo_critico_nuevo", "ops@x.co")
        _crear_regla(conn, tenant_id, "hallazgo_critico_nuevo", "seguridad@x.co")

        enviado = enviar_notificacion(
            conn, tenant_id, "hallazgo_critico_nuevo", "finding-6", "Asunto", "Cuerpo"
        )
        assert enviado is True

        filas = conn.execute(
            "SELECT destino FROM notifications WHERE tenant_id = ?", (tenant_id,)
        ).fetchall()
    finally:
        conn.close()
    # dos destinos distintos, ambos con la misma clave evento:recurso
    assert len(filas) == 2


# ---------------------------------------------------------------------------
# CRUD /notification-rules
# ---------------------------------------------------------------------------


def test_crud_notification_rules_crear_listar_activar_eliminar(client):
    token, _ = _registrar_tenant(client, "Tenant CRUD Notif", "notifcrud@notif.test")
    headers = {"Authorization": f"Bearer {token}"}

    creada = client.post(
        "/notification-rules",
        headers=headers,
        json={"evento": "hallazgo_critico_nuevo", "canal": "email", "destino": "ops@x.co"},
    )
    assert creada.status_code == 201
    body = creada.json()
    assert body["is_active"] is True
    rule_id = body["id"]

    listado = client.get("/notification-rules", headers=headers)
    assert listado.status_code == 200
    assert len(listado.json()) == 1

    desactivada = client.patch(
        f"/notification-rules/{rule_id}", headers=headers, json={"is_active": False}
    )
    assert desactivada.status_code == 200
    assert desactivada.json()["is_active"] is False

    eliminar = client.delete(f"/notification-rules/{rule_id}", headers=headers)
    assert eliminar.status_code == 204

    listado_final = client.get("/notification-rules", headers=headers)
    assert listado_final.json() == []


def test_regla_de_otro_tenant_responde_404(client):
    token_a, _ = _registrar_tenant(client, "Tenant Notif A", "notifa@notif.test")
    token_b, _ = _registrar_tenant(client, "Tenant Notif B", "notifb@notif.test")

    creada = client.post(
        "/notification-rules",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"evento": "hallazgo_critico_nuevo", "canal": "email", "destino": "ops@a.test"},
    )
    rule_id = creada.json()["id"]

    resp = client.patch(
        f"/notification-rules/{rule_id}",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"is_active": False},
    )
    assert resp.status_code == 404

    resp_del = client.delete(
        f"/notification-rules/{rule_id}", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert resp_del.status_code == 404


def test_canal_no_soportado_responde_422(client):
    token, _ = _registrar_tenant(client, "Tenant Notif Canal", "notifcanal@notif.test")
    resp = client.post(
        "/notification-rules",
        headers={"Authorization": f"Bearer {token}"},
        json={"evento": "hallazgo_critico_nuevo", "canal": "slack", "destino": "#alertas"},
    )
    assert resp.status_code == 422
