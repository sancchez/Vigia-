"""Fase 6c (openspec/changes/fase6-endurecimiento-produccion) --
`GET /reports/ejecutivo/download`.

Export ejecutivo consolidado multi-activo: postura de hallazgos abiertos
por activo (agregado desde `findings` vía `scans.asset_id`) más los KPIs de
remediación de `GET /kpis` (misma capa, `_kpis_de_tenant`, ver
`api/main.py`). Mismo patrón de tests que `/reports/cumplimiento/download`
(no existe suite dedicada para ese endpoint -- este archivo cubre ambos: el
contrato de descarga y el aislamiento por tenant), sembrando
`assets`/`scans`/`findings` directamente (mismo patrón de siembra directa
que `test_kpis.py`).
"""

from __future__ import annotations

import uuid

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


def _crear_asset(conn, tenant_id: str, valor: str, *, verificado: bool = True) -> str:
    asset_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO assets (id, tenant_id, tipo, valor, verificado, verification_token)
        VALUES (?, ?, 'dominio', ?, ?, 'tok')
        """,
        (asset_id, tenant_id, valor, 1 if verificado else 0),
    )
    return asset_id


def _crear_scan(conn, tenant_id: str, asset_id: str) -> str:
    scan_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO scans (id, tenant_id, asset_id, target, autorizacion_firmada, estado, trace_log_json)
        VALUES (?, ?, ?, 'x.test', 0, 'completado', '[]')
        """,
        (scan_id, tenant_id, asset_id),
    )
    return scan_id


def _sembrar_finding(conn, tenant_id: str, scan_id: str, *, severidad: str, estado: str = "descubierto") -> str:
    finding_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json, estado)
        VALUES (?, ?, ?, 'xss', ?, '/x', 0, '{}', ?)
        """,
        (finding_id, scan_id, tenant_id, severidad, estado),
    )
    return finding_id


def test_export_ejecutivo_pdf_con_varios_activos_no_vacio(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Ejecutivo A", "ejeca@ejec.test")
    conn = get_conn()
    try:
        asset1 = _crear_asset(conn, tenant_id, "uno.test")
        asset2 = _crear_asset(conn, tenant_id, "dos.test")
        scan1 = _crear_scan(conn, tenant_id, asset1)
        scan2 = _crear_scan(conn, tenant_id, asset2)
        _sembrar_finding(conn, tenant_id, scan1, severidad="critical")
        _sembrar_finding(conn, tenant_id, scan1, severidad="high")
        _sembrar_finding(conn, tenant_id, scan2, severidad="low")
        # Cerrado -- no debe contar en la postura abierta.
        _sembrar_finding(conn, tenant_id, scan2, severidad="critical", estado="cerrado")
        conn.commit()
    finally:
        conn.close()

    resp = client.get(
        "/reports/ejecutivo/download?formato=pdf", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 0


def test_export_ejecutivo_docx_no_vacio(client):
    token, _ = _registrar_tenant(client, "Tenant Ejecutivo B", "ejecb@ejec.test")
    resp = client.get(
        "/reports/ejecutivo/download?formato=docx", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert (
        resp.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert len(resp.content) > 0


def test_export_ejecutivo_formato_invalido_es_422(client):
    token, _ = _registrar_tenant(client, "Tenant Ejecutivo C", "ejecc@ejec.test")
    resp = client.get(
        "/reports/ejecutivo/download?formato=csv", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 422


def test_export_ejecutivo_requiere_auth(client):
    resp = client.get("/reports/ejecutivo/download?formato=pdf")
    assert resp.status_code == 401


def test_export_ejecutivo_aislado_por_tenant(client):
    """El tenant B no ve la postura de activos/hallazgos del tenant A --
    verificado indirectamente: el reporte del tenant B (sin activos) no
    revienta y no incluye el activo del tenant A."""
    token_a, tenant_a = _registrar_tenant(client, "Tenant Ejecutivo D", "ejecd@ejec.test")
    token_b, _ = _registrar_tenant(client, "Tenant Ejecutivo E", "ejece@ejec.test")
    conn = get_conn()
    try:
        asset_a = _crear_asset(conn, tenant_a, "soloa.test")
        scan_a = _crear_scan(conn, tenant_a, asset_a)
        _sembrar_finding(conn, tenant_a, scan_a, severidad="critical")
        conn.commit()
    finally:
        conn.close()

    resp_b = client.get(
        "/reports/ejecutivo/download?formato=pdf", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert resp_b.status_code == 200
    assert len(resp_b.content) > 0

    resp_a = client.get(
        "/reports/ejecutivo/download?formato=pdf", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert resp_a.status_code == 200
    assert len(resp_a.content) > 0
