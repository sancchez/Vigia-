"""Fase 2c (openspec/changes/fase2c-verificacion-cierre-y-kpis) --
`GET /kpis`.

Ejercita el cálculo puro en `api/main.py` (`_calcular_mttr_por_severidad`,
`_calcular_porcentaje_sla`, `_calcular_tendencia_por_activo`) a través del
endpoint real, sembrando `findings`/`scans`/`finding_transiciones`
directamente (mismo patrón de siembra directa que
`test_drift_reincidencia.py`/`test_finding_detail.py`, sin pasar por el
grafo LangGraph).

MTTR usa timestamps REALES de `finding_transiciones` (`fase2a-ciclo-vida-
hallazgo` ya está aplicado y persiste esa tabla con `created_at` real) --
no la aproximación con `ultima_vez_visto`/`created_at` que `design.md`
había dejado documentada como fallback.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from db.connection import get_conn


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123") -> str:
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    tenant_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["tenant_id"]
    return token, tenant_id


def _sembrar_finding(
    conn,
    tenant_id: str,
    scan_id: str,
    *,
    severidad: str = "high",
    estado: str = "descubierto",
    created_at: str,
    sla_due_at: str | None = None,
) -> str:
    finding_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO findings
            (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json,
             estado, created_at, sla_due_at)
        VALUES (?, ?, ?, 'xss', ?, '/x', 0, '{}', ?, ?, ?)
        """,
        (finding_id, scan_id, tenant_id, severidad, estado, created_at, sla_due_at),
    )
    return finding_id


def _registrar_cierre(conn, finding_id: str, tenant_id: str, cerrado_en: str) -> None:
    conn.execute(
        """
        INSERT INTO finding_transiciones
            (id, finding_id, tenant_id, estado_anterior, estado_nuevo, nota, changed_by, created_at)
        VALUES (?, ?, ?, 'verificado', 'cerrado', 'verificacion automatica', NULL, ?)
        """,
        (str(uuid.uuid4()), finding_id, tenant_id, cerrado_en),
    )


def _crear_scan(conn, tenant_id: str, asset_id: str | None, created_at: str) -> str:
    scan_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO scans (id, tenant_id, asset_id, target, autorizacion_firmada, estado, trace_log_json, created_at)
        VALUES (?, ?, ?, 'x.test', 0, 'completado', '[]', ?)
        """,
        (scan_id, tenant_id, asset_id, created_at),
    )
    return scan_id


# ---------------------------------------------------------------------------
# MTTR por severidad
# ---------------------------------------------------------------------------


def test_mttr_con_datos_disponibles_promedia_dias_reales(client):
    token, tenant_id = _registrar_tenant(client, "Tenant KPI A", "kpia@kpi.test")
    conn = get_conn()
    try:
        scan_id = _crear_scan(conn, tenant_id, None, "2026-01-01T00:00:00+00:00")
        # 3 críticos cerrados: deltas de 2, 4 y 6 días -> promedio 4.
        f1 = _sembrar_finding(
            conn, tenant_id, scan_id, severidad="critical", estado="cerrado",
            created_at="2026-01-01T00:00:00+00:00",
        )
        _registrar_cierre(conn, f1, tenant_id, "2026-01-03T00:00:00+00:00")
        f2 = _sembrar_finding(
            conn, tenant_id, scan_id, severidad="critical", estado="cerrado",
            created_at="2026-01-01T00:00:00+00:00",
        )
        _registrar_cierre(conn, f2, tenant_id, "2026-01-05T00:00:00+00:00")
        f3 = _sembrar_finding(
            conn, tenant_id, scan_id, severidad="critical", estado="cerrado",
            created_at="2026-01-01T00:00:00+00:00",
        )
        _registrar_cierre(conn, f3, tenant_id, "2026-01-07T00:00:00+00:00")
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/kpis", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    por_sev = {m["severidad"]: m for m in data["mttr_por_severidad"]}
    assert por_sev["critical"]["mttr_dias"] == 4.0
    assert por_sev["critical"]["precision"] == "exacto"


def test_mttr_sin_hallazgos_cerrados_de_una_severidad_es_null_no_cero(client):
    token, tenant_id = _registrar_tenant(client, "Tenant KPI B", "kpib@kpi.test")
    conn = get_conn()
    try:
        scan_id = _crear_scan(conn, tenant_id, None, "2026-01-01T00:00:00+00:00")
        # Un finding "low" que NUNCA se cerró -- no debe contar para el MTTR.
        _sembrar_finding(
            conn, tenant_id, scan_id, severidad="low", estado="descubierto",
            created_at="2026-01-01T00:00:00+00:00",
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/kpis", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    por_sev = {m["severidad"]: m for m in resp.json()["mttr_por_severidad"]}
    assert por_sev["low"]["mttr_dias"] is None
    # Todas las 5 severidades siempre aparecen, incluso sin datos.
    assert set(por_sev.keys()) == {"critical", "high", "medium", "low", "info"}


def test_mttr_usa_created_at_de_finding_transiciones_no_formato_sqlite_default(client):
    """`finding_transiciones.created_at` insertado vía `PATCH /findings/{id}`
    (fase2a) deja el `DEFAULT CURRENT_TIMESTAMP` sin offset (a diferencia
    del formato ISO con offset que usa `POST /findings/{id}/verificar-
    cierre` o la detección de reincidencia) -- confirma que
    `_parse_dt_utc_naive` no revienta con ese formato mixto y que el MTTR
    sale un número real, no una excepción."""
    token, tenant_id = _registrar_tenant(client, "Tenant KPI C", "kpic@kpi.test")
    conn = get_conn()
    try:
        scan_id = _crear_scan(conn, tenant_id, None, "2026-01-01T00:00:00+00:00")
        finding_id = _sembrar_finding(
            conn, tenant_id, scan_id, severidad="high", estado="verificado",
            created_at="2026-01-01T00:00:00+00:00",
        )
        conn.commit()
    finally:
        conn.close()

    # Transición real vía PATCH (deja el DEFAULT CURRENT_TIMESTAMP, formato
    # "YYYY-MM-DD HH:MM:SS" sin offset) -- verificado -> cerrado es válida
    # directo desde TRANSICIONES_VALIDAS.
    resp_patch = client.patch(
        f"/findings/{finding_id}",
        json={"estado": "cerrado", "nota": "remediado y confirmado"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_patch.status_code == 200

    resp = client.get("/kpis", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200  # no debe lanzar TypeError por mezclar formatos
    por_sev = {m["severidad"]: m for m in resp.json()["mttr_por_severidad"]}
    assert por_sev["high"]["mttr_dias"] is not None
    assert por_sev["high"]["mttr_dias"] >= 0


# ---------------------------------------------------------------------------
# % dentro de SLA
# ---------------------------------------------------------------------------


def test_porcentaje_sla_con_todos_los_abiertos_dentro_de_plazo_es_100(client):
    token, tenant_id = _registrar_tenant(client, "Tenant KPI D", "kpid@kpi.test")
    futuro = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    conn = get_conn()
    try:
        scan_id = _crear_scan(conn, tenant_id, None, "2026-01-01T00:00:00+00:00")
        for _ in range(5):
            _sembrar_finding(
                conn, tenant_id, scan_id, severidad="high", estado="triage",
                created_at="2026-01-01T00:00:00+00:00", sla_due_at=futuro,
            )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/kpis", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["porcentaje_dentro_sla"] == 100.0


def test_porcentaje_sla_sin_hallazgos_abiertos_es_null_no_division_por_cero(client):
    token, tenant_id = _registrar_tenant(client, "Tenant KPI E", "kpie@kpi.test")
    conn = get_conn()
    try:
        scan_id = _crear_scan(conn, tenant_id, None, "2026-01-01T00:00:00+00:00")
        # Único finding, ya cerrado -- no cuenta como "abierto".
        _sembrar_finding(
            conn, tenant_id, scan_id, severidad="high", estado="cerrado",
            created_at="2026-01-01T00:00:00+00:00",
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/kpis", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["porcentaje_dentro_sla"] is None


def test_porcentaje_sla_tenant_sin_ningun_finding_es_null(client):
    token, _tenant_id = _registrar_tenant(client, "Tenant KPI F", "kpif@kpi.test")
    resp = client.get("/kpis", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["porcentaje_dentro_sla"] is None
    assert resp.json()["tendencia_por_activo"] == []


# ---------------------------------------------------------------------------
# Tendencia de riesgo por activo
# ---------------------------------------------------------------------------


def test_tendencia_por_activo_con_tres_scans_historicos(client):
    token, tenant_id = _registrar_tenant(client, "Tenant KPI G", "kpig@kpi.test")
    conn = get_conn()
    try:
        asset_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO assets (id, tenant_id, tipo, valor, verification_token) VALUES (?, ?, 'dominio', 'kpig.test', 'tok')",
            (asset_id, tenant_id),
        )
        scan1 = _crear_scan(conn, tenant_id, asset_id, "2026-01-01T00:00:00+00:00")
        _sembrar_finding(conn, tenant_id, scan1, severidad="high", estado="descubierto", created_at="2026-01-01T00:00:00+00:00")

        scan2 = _crear_scan(conn, tenant_id, asset_id, "2026-02-01T00:00:00+00:00")
        _sembrar_finding(conn, tenant_id, scan2, severidad="critical", estado="descubierto", created_at="2026-02-01T00:00:00+00:00")
        _sembrar_finding(conn, tenant_id, scan2, severidad="high", estado="descubierto", created_at="2026-02-01T00:00:00+00:00")

        scan3 = _crear_scan(conn, tenant_id, asset_id, "2026-03-01T00:00:00+00:00")
        _sembrar_finding(conn, tenant_id, scan3, severidad="low", estado="cerrado", created_at="2026-03-01T00:00:00+00:00")
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/kpis", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    tendencias = resp.json()["tendencia_por_activo"]
    assert len(tendencias) == 1
    serie = tendencias[0]
    assert serie["asset_id"] == asset_id
    # scan3 solo tenía un finding "cerrado" (no cuenta como abierto) -- 2
    # puntos, no 3.
    assert len(serie["puntos"]) == 2
    punto_scan2 = next(p for p in serie["puntos"] if p["fecha"] == "2026-02-01T00:00:00+00:00")
    assert punto_scan2["critical"] == 1
    assert punto_scan2["high"] == 1
