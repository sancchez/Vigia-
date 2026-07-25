"""Fase 7 (openspec/changes/fase7-confianza-calidad) -- `GET /scans/{id}/trace`
y `GET /audit`.

Ambos endpoints son de solo lectura sobre `scans.trace_log_json`, ya
persistido por el grafo (`orchestrator/state.py::make_trace_event`) desde
antes de esta fase -- nada de esto escribe o muta el trace_log. Sembramos
scans directo en la tabla (mismo patrón que
`tests/test_multi_tenant_isolation.py`/`tests/test_kpis.py`, sin pasar por
el grafo LangGraph) con `trace_log_json` ya poblado, para ejercitar la
proyección de los endpoints y el aislamiento por tenant.
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
    tenant_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["tenant_id"]
    return token, tenant_id


def _sembrar_scan(tenant_id: str, target: str, trace_log: list[dict]) -> str:
    conn = get_conn()
    try:
        scan_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado, trace_log_json) "
            "VALUES (?, ?, ?, 0, 'completado', ?)",
            (scan_id, tenant_id, target, json.dumps(trace_log, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return scan_id


def _evento(agente: str, accion: str, resultado: str, timestamp: str) -> dict:
    return {"timestamp": timestamp, "agente": agente, "accion": accion, "resultado": resultado}


def test_traza_de_un_scan_propio(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Traza A", "traza-a@vigia.test")
    trace = [
        _evento("recon", "descubrir_endpoints", "5 endpoints encontrados", "2026-01-01T10:00:00+00:00"),
        _evento("escaneo", "correr_nuclei", "2 hallazgos", "2026-01-01T10:05:00+00:00"),
    ]
    scan_id = _sembrar_scan(tenant_id, "http://traza-a.test/", trace)

    resp = client.get(f"/scans/{scan_id}/trace", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    eventos = resp.json()
    assert len(eventos) == 2
    assert eventos[0]["agente"] == "recon"
    assert eventos[0]["accion"] == "descubrir_endpoints"
    assert eventos[1]["resultado"] == "2 hallazgos"
    for ev in eventos:
        assert set(ev.keys()) == {"timestamp", "agente", "accion", "resultado"}


def test_traza_scan_vacia_devuelve_200_lista_vacia(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Traza Vacia", "traza-vacia@vigia.test")
    scan_id = _sembrar_scan(tenant_id, "http://traza-vacia.test/", [])

    resp = client.get(f"/scans/{scan_id}/trace", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_traza_de_scan_de_otro_tenant_da_404(client):
    token_a, tenant_a = _registrar_tenant(client, "Tenant Traza B", "traza-b1@vigia.test")
    _, tenant_b = _registrar_tenant(client, "Tenant Traza C", "traza-b2@vigia.test")
    scan_id_b = _sembrar_scan(tenant_b, "http://traza-b.test/", [_evento("recon", "x", "y", "2026-01-01T00:00:00+00:00")])

    resp = client.get(f"/scans/{scan_id_b}/trace", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 404


def test_traza_scan_inexistente_da_404(client):
    token, _ = _registrar_tenant(client, "Tenant Traza D", "traza-d@vigia.test")
    resp = client.get(f"/scans/{uuid.uuid4()}/trace", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_auditoria_agrega_eventos_de_varios_scans_mas_reciente_primero(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Audit Multi", "audit-multi@vigia.test")
    scan_1 = _sembrar_scan(
        tenant_id,
        "http://audit-1.test/",
        [_evento("recon", "descubrir_endpoints", "ok", "2026-01-01T09:00:00+00:00")],
    )
    scan_2 = _sembrar_scan(
        tenant_id,
        "http://audit-2.test/",
        [_evento("escaneo", "correr_zap", "ok", "2026-01-02T09:00:00+00:00")],
    )

    resp = client.get("/audit", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    eventos = resp.json()
    assert len(eventos) == 2
    scan_ids = {e["scan_id"] for e in eventos}
    assert scan_ids == {scan_1, scan_2}
    # más reciente primero (2026-01-02 antes que 2026-01-01)
    assert eventos[0]["scan_id"] == scan_2
    assert eventos[0]["target"] == "http://audit-2.test/"


def test_auditoria_aisla_eventos_entre_tenants(client):
    token_a, tenant_a = _registrar_tenant(client, "Tenant Audit Iso A", "audit-iso-a@vigia.test")
    _, tenant_b = _registrar_tenant(client, "Tenant Audit Iso B", "audit-iso-b@vigia.test")

    _sembrar_scan(tenant_a, "http://audit-iso-a.test/", [_evento("recon", "x", "propio de A", "2026-01-01T00:00:00+00:00")])
    _sembrar_scan(tenant_b, "http://audit-iso-b.test/", [_evento("recon", "x", "propio de B", "2026-01-01T00:00:00+00:00")])

    resp_a = client.get("/audit", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_a.status_code == 200
    eventos_a = resp_a.json()
    assert all(e["resultado"] != "propio de B" for e in eventos_a)
    assert any(e["resultado"] == "propio de A" for e in eventos_a)
