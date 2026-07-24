"""Fase 2a (openspec/changes/fase2a-ciclo-vida-hallazgo) -- detección de
reincidencia (drift) al insertar findings de un scan nuevo.

Ejercita `_registrar_findings_con_ciclo_de_vida` directamente (Python plano,
sin pasar por el grafo LangGraph ni por `POST /scan`), sembrando
assets/scans a mano -- mismo patrón de siembra directa que
`test_finding_detail.py` / `test_multi_tenant_isolation.py`.
"""

from __future__ import annotations

import json
import uuid

from api.main import _registrar_findings_con_ciclo_de_vida
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


def _crear_asset(conn, tenant_id: str, valor: str = "drift.test") -> str:
    asset_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO assets (id, tenant_id, tipo, valor, verification_token) "
        "VALUES (?, ?, 'dominio', ?, 'tok-drift')",
        (asset_id, tenant_id, valor),
    )
    return asset_id


def _crear_scan(conn, tenant_id: str, asset_id: str | None, target: str = "drift.test") -> str:
    scan_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO scans (id, tenant_id, asset_id, target, autorizacion_firmada, estado, trace_log_json) "
        "VALUES (?, ?, ?, ?, 0, 'completado', '[]')",
        (scan_id, tenant_id, asset_id, target),
    )
    return scan_id


def _hallazgo(tipo: str = "XSS", endpoint: str = "/login", severidad: str = "high") -> dict:
    return {
        "tipo": tipo,
        "severidad": severidad,
        "endpoint": endpoint,
        "confirmado": False,
        "raw_json": json.dumps({"tipo": tipo, "endpoint": endpoint}),
    }


def test_segundo_scan_mismo_asset_tipo_endpoint_actualiza_no_duplica(client):
    tenant_id = _registrar_tenant(client, "Tenant Drift A", "drifta@drift.test")
    conn = get_conn()
    try:
        asset_id = _crear_asset(conn, tenant_id)
        scan1 = _crear_scan(conn, tenant_id, asset_id)
        conn.commit()
        _registrar_findings_con_ciclo_de_vida(
            conn, tenant_id, scan1, asset_id, [_hallazgo()], "2026-01-01T00:00:00+00:00"
        )
        conn.commit()

        scan2 = _crear_scan(conn, tenant_id, asset_id)
        conn.commit()
        _registrar_findings_con_ciclo_de_vida(
            conn, tenant_id, scan2, asset_id, [_hallazgo()], "2026-02-01T00:00:00+00:00"
        )
        conn.commit()

        rows = conn.execute("SELECT * FROM findings WHERE tenant_id = ?", (tenant_id,)).fetchall()
        assert len(rows) == 1
        fila = dict(rows[0])
        assert fila["ultima_vez_visto"] == "2026-02-01T00:00:00+00:00"
        # Sigue colgado del scan original -- no se reinsertó bajo scan2.
        assert fila["scan_id"] == scan1
    finally:
        conn.close()


def test_finding_cerrado_reaparece_vuelve_a_descubierto_con_transicion_registrada(client):
    tenant_id = _registrar_tenant(client, "Tenant Drift B", "driftb@drift.test")
    conn = get_conn()
    try:
        asset_id = _crear_asset(conn, tenant_id)
        scan1 = _crear_scan(conn, tenant_id, asset_id)
        conn.commit()
        _registrar_findings_con_ciclo_de_vida(
            conn, tenant_id, scan1, asset_id, [_hallazgo()], "2026-01-01T00:00:00+00:00"
        )
        conn.commit()

        finding_id = dict(
            conn.execute("SELECT * FROM findings WHERE tenant_id = ?", (tenant_id,)).fetchone()
        )["id"]
        conn.execute("UPDATE findings SET estado = 'cerrado' WHERE id = ?", (finding_id,))
        conn.commit()

        scan2 = _crear_scan(conn, tenant_id, asset_id)
        conn.commit()
        _registrar_findings_con_ciclo_de_vida(
            conn, tenant_id, scan2, asset_id, [_hallazgo()], "2026-02-01T00:00:00+00:00"
        )
        conn.commit()

        fila = dict(conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone())
        assert fila["estado"] == "descubierto"
        assert fila["ultima_vez_visto"] == "2026-02-01T00:00:00+00:00"

        transicion = dict(
            conn.execute(
                "SELECT * FROM finding_transiciones WHERE finding_id = ?", (finding_id,)
            ).fetchone()
        )
        assert transicion["estado_anterior"] == "cerrado"
        assert transicion["estado_nuevo"] == "descubierto"
        assert transicion["changed_by"] is None
        assert "reincidencia" in transicion["nota"].lower()
        # Timestamp real de la transición, no aproximado -- importante para
        # que fase2c pueda calcular MTTR con precisión.
        assert transicion["created_at"] is not None
    finally:
        conn.close()


def test_scan_con_asset_id_null_no_genera_falso_match_ni_rompe(client):
    tenant_id = _registrar_tenant(client, "Tenant Drift C", "driftc@drift.test")
    conn = get_conn()
    try:
        scan1 = _crear_scan(conn, tenant_id, None)
        conn.commit()
        _registrar_findings_con_ciclo_de_vida(
            conn, tenant_id, scan1, None, [_hallazgo()], "2026-01-01T00:00:00+00:00"
        )
        conn.commit()

        scan2 = _crear_scan(conn, tenant_id, None)
        conn.commit()
        _registrar_findings_con_ciclo_de_vida(
            conn, tenant_id, scan2, None, [_hallazgo()], "2026-02-01T00:00:00+00:00"
        )
        conn.commit()

        rows = conn.execute("SELECT * FROM findings WHERE tenant_id = ?", (tenant_id,)).fetchall()
        # Sin asset_id resoluble, cada llamada trata el hallazgo como nuevo
        # (nunca un match falso) -- 2 filas, no 1.
        assert len(rows) == 2
    finally:
        conn.close()


def test_hallazgo_nuevo_sin_match_setea_primera_y_ultima_vez_visto_iguales(client):
    tenant_id = _registrar_tenant(client, "Tenant Drift D", "driftd@drift.test")
    conn = get_conn()
    try:
        asset_id = _crear_asset(conn, tenant_id)
        scan1 = _crear_scan(conn, tenant_id, asset_id)
        conn.commit()
        now = "2026-03-01T00:00:00+00:00"
        _registrar_findings_con_ciclo_de_vida(conn, tenant_id, scan1, asset_id, [_hallazgo()], now)
        conn.commit()

        fila = dict(conn.execute("SELECT * FROM findings WHERE tenant_id = ?", (tenant_id,)).fetchone())
        assert fila["primera_vez_visto"] == now
        assert fila["ultima_vez_visto"] == now
        assert fila["sla_due_at"] is not None  # severidad "high" -> SLA default de 30 días
    finally:
        conn.close()


def test_finding_activo_reincidente_actualiza_ultima_vez_visto_sin_transicion(client):
    """Un finding en un estado ACTIVO (no cerrado/verificado/aceptado_riesgo)
    que reaparece solo actualiza `ultima_vez_visto` -- no genera una fila en
    `finding_transiciones` (no hay cambio de estado que auditar)."""
    tenant_id = _registrar_tenant(client, "Tenant Drift E", "drifte@drift.test")
    conn = get_conn()
    try:
        asset_id = _crear_asset(conn, tenant_id)
        scan1 = _crear_scan(conn, tenant_id, asset_id)
        conn.commit()
        _registrar_findings_con_ciclo_de_vida(
            conn, tenant_id, scan1, asset_id, [_hallazgo()], "2026-01-01T00:00:00+00:00"
        )
        conn.commit()
        finding_id = dict(
            conn.execute("SELECT * FROM findings WHERE tenant_id = ?", (tenant_id,)).fetchone()
        )["id"]

        scan2 = _crear_scan(conn, tenant_id, asset_id)
        conn.commit()
        _registrar_findings_con_ciclo_de_vida(
            conn, tenant_id, scan2, asset_id, [_hallazgo()], "2026-01-15T00:00:00+00:00"
        )
        conn.commit()

        fila = dict(conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone())
        assert fila["estado"] == "descubierto"
        assert fila["ultima_vez_visto"] == "2026-01-15T00:00:00+00:00"

        transiciones = conn.execute(
            "SELECT * FROM finding_transiciones WHERE finding_id = ?", (finding_id,)
        ).fetchall()
        assert len(transiciones) == 0
    finally:
        conn.close()
