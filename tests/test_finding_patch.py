"""Fase 2a (openspec/changes/fase2a-ciclo-vida-hallazgo) -- `PATCH /findings/{id}`.

Un caso por escenario de spec.md: máquina de estados, salida lateral a
`aceptado_riesgo`, nota obligatoria en cambio de estado, reasignación de
dueño (con validación de tenant), scoping por tenant (404), y autenticación.
"""

from __future__ import annotations

import uuid

from auth.jwt_auth import get_password_hash
from db.connection import get_conn


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123"):
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    tenant_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["tenant_id"]
    return token, tenant_id


def _sembrar_finding(tenant_id: str, estado: str = "descubierto") -> str:
    conn = get_conn()
    try:
        scan_id = str(uuid.uuid4())
        finding_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado, trace_log_json) "
            "VALUES (?, ?, 'x.test', 0, 'completado', '[]')",
            (scan_id, tenant_id),
        )
        conn.execute(
            "INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json, estado) "
            "VALUES (?, ?, ?, 'xss', 'high', '/login', 0, '{}', ?)",
            (finding_id, scan_id, tenant_id, estado),
        )
        conn.commit()
    finally:
        conn.close()
    return finding_id


def _sembrar_usuario_del_tenant(tenant_id: str, email: str) -> str:
    """Segundo usuario del mismo tenant, sembrado directo en la DB -- el
    flujo real de invitaciones no es relevante para estos tests."""
    conn = get_conn()
    try:
        user_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO users (id, tenant_id, email, hashed_password, role) VALUES (?, ?, ?, ?, 'member')",
            (user_id, tenant_id, email, get_password_hash("claveSegura123")),
        )
        conn.commit()
    finally:
        conn.close()
    return user_id


def test_transicion_valida_con_nota_200_y_registra_transicion(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Patch A", "patcha@patch.test")
    finding_id = _sembrar_finding(tenant_id, estado="descubierto")

    resp = client.patch(
        f"/findings/{finding_id}",
        json={"estado": "triage", "nota": "revisado por seguridad"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "triage"

    conn = get_conn()
    try:
        fila = dict(
            conn.execute(
                "SELECT * FROM finding_transiciones WHERE finding_id = ?", (finding_id,)
            ).fetchone()
        )
    finally:
        conn.close()
    assert fila["estado_anterior"] == "descubierto"
    assert fila["estado_nuevo"] == "triage"
    assert fila["nota"] == "revisado por seguridad"
    assert fila["created_at"] is not None


def test_transicion_invalida_409_y_estado_no_cambia(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Patch B", "patchb@patch.test")
    finding_id = _sembrar_finding(tenant_id, estado="descubierto")

    resp = client.patch(
        f"/findings/{finding_id}",
        json={"estado": "cerrado"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409

    conn = get_conn()
    try:
        fila = dict(conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone())
    finally:
        conn.close()
    assert fila["estado"] == "descubierto"


def test_salida_lateral_a_aceptado_riesgo_desde_estado_activo_200(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Patch C", "patchc@patch.test")
    finding_id = _sembrar_finding(tenant_id, estado="en_progreso")

    resp = client.patch(
        f"/findings/{finding_id}",
        json={"estado": "aceptado_riesgo", "nota": "riesgo aceptado por negocio"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "aceptado_riesgo"


def test_nota_obligatoria_en_cambio_de_estado_422(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Patch D", "patchd@patch.test")
    finding_id = _sembrar_finding(tenant_id, estado="triage")

    resp = client.patch(
        f"/findings/{finding_id}",
        json={"estado": "asignado"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_reasignar_owner_sin_cambiar_estado_200_sin_nota(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Patch E", "patche@patch.test")
    finding_id = _sembrar_finding(tenant_id, estado="descubierto")
    owner_id = _sembrar_usuario_del_tenant(tenant_id, "miembro@patch.test")

    resp = client.patch(
        f"/findings/{finding_id}",
        json={"owner_user_id": owner_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["owner_user_id"] == owner_id
    assert data["owner_email"] == "miembro@patch.test"
    assert data["estado"] == "descubierto"


def test_owner_ajeno_al_tenant_422(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Patch F", "patchf@patch.test")
    _, otro_tenant_id = _registrar_tenant(client, "Tenant Patch F Otro", "otro@patch.test")
    finding_id = _sembrar_finding(tenant_id, estado="descubierto")
    owner_ajeno = _sembrar_usuario_del_tenant(otro_tenant_id, "ajeno@patch.test")

    resp = client.patch(
        f"/findings/{finding_id}",
        json={"owner_user_id": owner_ajeno},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_finding_de_otro_tenant_404_no_403(client):
    token_dueno, tenant_dueno = _registrar_tenant(client, "Tenant Patch G Dueno", "duenog@patch.test")
    token_intruso, _ = _registrar_tenant(client, "Tenant Patch G Intruso", "intrusog@patch.test")
    finding_id = _sembrar_finding(tenant_dueno, estado="descubierto")

    resp = client.patch(
        f"/findings/{finding_id}",
        json={"estado": "triage", "nota": "x"},
        headers={"Authorization": f"Bearer {token_intruso}"},
    )
    assert resp.status_code == 404


def test_patch_id_inexistente_404(client):
    token, _tenant_id = _registrar_tenant(client, "Tenant Patch Inexistente", "inexistente@patch.test")
    resp = client.patch(
        f"/findings/{uuid.uuid4()}",
        json={"estado": "triage", "nota": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_patch_sin_token_401_o_403(client):
    resp = client.patch(f"/findings/{uuid.uuid4()}", json={"estado": "triage", "nota": "x"})
    assert resp.status_code in (401, 403)


def test_patch_con_token_invalido_401(client):
    resp = client.patch(
        f"/findings/{uuid.uuid4()}",
        json={"estado": "triage", "nota": "x"},
        headers={"Authorization": "Bearer token-invalido-no-es-jwt"},
    )
    assert resp.status_code == 401


def test_patch_body_vacio_422(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Patch H", "patchh@patch.test")
    finding_id = _sembrar_finding(tenant_id, estado="descubierto")

    resp = client.patch(
        f"/findings/{finding_id}",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_estado_desconocido_422(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Patch I", "patchi@patch.test")
    finding_id = _sembrar_finding(tenant_id, estado="descubierto")

    resp = client.patch(
        f"/findings/{finding_id}",
        json={"estado": "no_existe", "nota": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
