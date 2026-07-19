"""Aislamiento multi-tenant en `GET /findings` y `GET /reports/cumplimiento`.

Ver docs/produccion-readiness.md, punto 5 de la lista de tests
prioritarios: "Ya se verificó manualmente una vez (Item 6, Item 5) que los
datos de un tenant no se filtran a otro -- es exactamente el tipo de bug
que un cambio futuro descuidado (un `WHERE tenant_id = ?` olvidado en una
query nueva) reintroduciría en silencio sin un test que lo agarre."

Sembramos hallazgos directo en la tabla `findings` (bypassando el grafo
LangGraph -- no hace falta invocar Nuclei/ZAP/Claude para probar un filtro
SQL) para dos tenants registrados de verdad vía `POST /auth/register`, y
confirmamos que ninguno de los dos endpoints filtra datos entre tenants.
"""

from __future__ import annotations

import json
import uuid

from db.connection import get_conn


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123"):
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    return token, me.json()["tenant_id"]


def _sembrar_finding(tenant_id: str, tipo: str, severidad: str, endpoint: str, alert: str) -> None:
    """Inserta un scan + finding reales para un tenant, sin pasar por el grafo."""
    conn = get_conn()
    try:
        scan_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado, trace_log_json) "
            "VALUES (?, ?, ?, 0, 'completado', '[]')",
            (scan_id, tenant_id, endpoint),
        )
        conn.execute(
            "INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (
                str(uuid.uuid4()),
                scan_id,
                tenant_id,
                tipo,
                severidad,
                endpoint,
                json.dumps({"herramienta": "zap-baseline", "raw": {"alert": alert}}),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_findings_de_un_tenant_nunca_aparecen_en_otro(client):
    token_a, tenant_a = _registrar_tenant(client, "Tenant Aislamiento A", "a@aislamiento.test")
    token_b, tenant_b = _registrar_tenant(client, "Tenant Aislamiento B", "b@aislamiento.test")

    _sembrar_finding(
        tenant_a, "desconocido", "medium", "http://tenant-a.test/", "CSP Header Not Set"
    )
    _sembrar_finding(
        tenant_b, "desconocido", "critical", "http://tenant-b.test/login", "SQL Injection"
    )

    findings_a = client.get("/findings", headers={"Authorization": f"Bearer {token_a}"})
    findings_b = client.get("/findings", headers={"Authorization": f"Bearer {token_b}"})
    assert findings_a.status_code == 200
    assert findings_b.status_code == 200
    findings_a = findings_a.json()
    findings_b = findings_b.json()

    assert len(findings_a) == 1
    assert findings_a[0]["endpoint"] == "http://tenant-a.test/"
    assert all(f["endpoint"] != "http://tenant-b.test/login" for f in findings_a)

    assert len(findings_b) == 1
    assert findings_b[0]["endpoint"] == "http://tenant-b.test/login"
    assert all(f["endpoint"] != "http://tenant-a.test/" for f in findings_b)


def test_reporte_cumplimiento_de_un_tenant_nunca_incluye_hallazgos_de_otro(client):
    token_a, tenant_a = _registrar_tenant(client, "Tenant Cumplimiento A", "cumpla@aislamiento.test")
    token_b, tenant_b = _registrar_tenant(client, "Tenant Cumplimiento B", "cumplb@aislamiento.test")

    _sembrar_finding(
        tenant_a,
        "desconocido",
        "medium",
        "http://tenant-cumpla.test/",
        "CSP Header Not Set",
    )
    _sembrar_finding(
        tenant_b,
        "desconocido",
        "critical",
        "http://tenant-cumplb.test/admin",
        "SQL Injection",
    )

    reporte_a = client.get("/reports/cumplimiento", headers={"Authorization": f"Bearer {token_a}"})
    reporte_b = client.get("/reports/cumplimiento", headers={"Authorization": f"Bearer {token_b}"})
    assert reporte_a.status_code == 200
    assert reporte_b.status_code == 200
    reporte_a = reporte_a.json()
    reporte_b = reporte_b.json()

    assert reporte_a["total_hallazgos"] == 1
    assert reporte_b["total_hallazgos"] == 1

    endpoints_a = {
        ejemplo.get("endpoint")
        for categoria in reporte_a["resumen_por_categoria"]
        for ejemplo in categoria["ejemplos"]
    }
    endpoints_b = {
        ejemplo.get("endpoint")
        for categoria in reporte_b["resumen_por_categoria"]
        for ejemplo in categoria["ejemplos"]
    }

    assert "http://tenant-cumplb.test/admin" not in endpoints_a
    assert "http://tenant-cumpla.test/" not in endpoints_b
    assert endpoints_a == {"http://tenant-cumpla.test/"}
    assert endpoints_b == {"http://tenant-cumplb.test/admin"}
