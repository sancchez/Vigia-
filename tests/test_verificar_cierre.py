"""Fase 2c (openspec/changes/fase2c-verificacion-cierre-y-kpis) --
`POST /findings/{id}/verificar-cierre`.

Un caso por escenario de spec.md: dispatch por herramienta de origen
(nuclei/zap/no-soportado), transición automática cuando el hallazgo ya no
reproduce, `estado` sin cambios cuando persiste, falla de infraestructura
(ZAP/Docker no disponible) sin corromper `estado`, y scoping por tenant
(404, no 403). Todas las llamadas de red/Docker se mockean directo sobre
`api.main` (donde este módulo importa `run_nuclei`/
`run_single_target_verification` a su propio namespace) -- deterministas y
rápidos, sin depender de Docker/ZAP/nuclei reales.
"""

from __future__ import annotations

import json
import uuid

import pytest

import api.main as api_main
from db.connection import get_conn
from tools._shared import ToolNotInstalledError
from tools.scan import ScanResult
from tools.zap_api import ZapCheckpointedResult, ZapDaemonError


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123"):
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    tenant_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["tenant_id"]
    return token, tenant_id


def _sembrar_finding(
    tenant_id: str,
    *,
    estado: str = "mitigado",
    endpoint: str = "http://x.test/login",
    raw_json: dict | None = None,
) -> str:
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
            "VALUES (?, ?, ?, 'xss', 'high', ?, 0, ?, ?)",
            (finding_id, scan_id, tenant_id, endpoint, json.dumps(raw_json or {}), estado),
        )
        conn.commit()
    finally:
        conn.close()
    return finding_id


def _finding_row(finding_id: str) -> dict:
    conn = get_conn()
    try:
        return dict(conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone())
    finally:
        conn.close()


def _transiciones_de(finding_id: str) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM finding_transiciones WHERE finding_id = ? ORDER BY id", (finding_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dispatch + transición automática cuando no reaparece
# ---------------------------------------------------------------------------


def test_nuclei_no_reaparece_cierra_automaticamente_y_registra_transiciones(client, monkeypatch):
    token, tenant_id = _registrar_tenant(client, "Tenant Verif A", "verifa@verif.test")
    finding_id = _sembrar_finding(
        tenant_id, estado="mitigado", raw_json={"herramienta": "nuclei", "raw": {}}
    )

    monkeypatch.setattr(
        api_main,
        "run_nuclei",
        lambda target: ScanResult(target=target, tool="nuclei", findings=[]),
    )

    resp = client.post(
        f"/findings/{finding_id}/verificar-cierre", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["estado"] == "cerrado"
    assert data["reaparece"] is False

    fila = _finding_row(finding_id)
    assert fila["estado"] == "cerrado"

    transiciones = _transiciones_de(finding_id)
    assert len(transiciones) == 2
    por_nuevo = {t["estado_nuevo"]: t for t in transiciones}
    assert por_nuevo["verificado"]["estado_anterior"] == "mitigado"
    assert por_nuevo["cerrado"]["estado_anterior"] == "verificado"
    for t in transiciones:
        assert t["changed_by"] is not None
        assert t["created_at"] is not None


def test_desde_verificado_no_genera_transicion_autoreferencial(client, monkeypatch):
    """Si el hallazgo ya estaba en 'verificado' (ej. lo verificó a mano
    alguien vía PATCH antes de llamar a este endpoint), el paso intermedio
    se salta -- solo una fila de transición ('verificado' -> 'cerrado'), no
    una auto-referencial ('verificado' -> 'verificado')."""
    token, tenant_id = _registrar_tenant(client, "Tenant Verif H", "verifh@verif.test")
    finding_id = _sembrar_finding(
        tenant_id, estado="verificado", raw_json={"herramienta": "nuclei", "raw": {}}
    )

    monkeypatch.setattr(
        api_main,
        "run_nuclei",
        lambda target: ScanResult(target=target, tool="nuclei", findings=[]),
    )

    resp = client.post(
        f"/findings/{finding_id}/verificar-cierre", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "cerrado"

    transiciones = _transiciones_de(finding_id)
    assert len(transiciones) == 1
    assert transiciones[0]["estado_anterior"] == "verificado"
    assert transiciones[0]["estado_nuevo"] == "cerrado"


def test_zap_alertas_planas_sin_wrapper_no_reaparece_cierra(client, monkeypatch):
    """Ausencia de `herramienta` en `raw_json` (alertas planas de escaneo
    activo checkpointed) se dispatchea como ZAP -- ver
    `_herramienta_de_finding`."""
    token, tenant_id = _registrar_tenant(client, "Tenant Verif B", "verifb@verif.test")
    finding_id = _sembrar_finding(tenant_id, estado="mitigado", raw_json={"description": "XSS"})

    monkeypatch.setattr(
        api_main,
        "run_single_target_verification",
        lambda **kwargs: ZapCheckpointedResult(target=kwargs["endpoint_url"], findings=[]),
    )

    resp = client.post(
        f"/findings/{finding_id}/verificar-cierre", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["estado"] == "cerrado"
    assert data["reaparece"] is False
    assert _finding_row(finding_id)["estado"] == "cerrado"


# ---------------------------------------------------------------------------
# Hallazgo persiste: estado sin cambio
# ---------------------------------------------------------------------------


def test_hallazgo_persiste_reaparece_true_y_estado_no_cambia(client, monkeypatch):
    token, tenant_id = _registrar_tenant(client, "Tenant Verif C", "verifc@verif.test")
    finding_id = _sembrar_finding(
        tenant_id, estado="mitigado", raw_json={"herramienta": "nuclei", "raw": {}}
    )

    monkeypatch.setattr(
        api_main,
        "run_nuclei",
        lambda target: ScanResult(
            target=target, tool="nuclei", findings=[{"info": {"description": "sigue ahí"}}]
        ),
    )

    resp = client.post(
        f"/findings/{finding_id}/verificar-cierre", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["estado"] == "mitigado"
    assert data["reaparece"] is True

    fila = _finding_row(finding_id)
    assert fila["estado"] == "mitigado"
    # raw_json se refresca con estado_verificacion nuevo (task 2.4), sin
    # generar ninguna fila de transición (no hubo cambio de estado).
    raw = json.loads(fila["raw_json"])
    assert "estado_verificacion" in raw
    assert _transiciones_de(finding_id) == []


# ---------------------------------------------------------------------------
# Falla de infraestructura: 503, estado sin cambio
# ---------------------------------------------------------------------------


def test_zap_no_disponible_503_y_estado_sin_cambio(client, monkeypatch):
    token, tenant_id = _registrar_tenant(client, "Tenant Verif D", "verifd@verif.test")
    finding_id = _sembrar_finding(tenant_id, estado="mitigado", raw_json={})

    def _lanza_error(**kwargs):
        raise ZapDaemonError("el daemon de ZAP no respondió")

    monkeypatch.setattr(api_main, "run_single_target_verification", _lanza_error)

    resp = client.post(
        f"/findings/{finding_id}/verificar-cierre", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 503
    assert _finding_row(finding_id)["estado"] == "mitigado"


def test_nuclei_no_instalado_503_y_estado_sin_cambio(client, monkeypatch):
    token, tenant_id = _registrar_tenant(client, "Tenant Verif E", "verife@verif.test")
    finding_id = _sembrar_finding(
        tenant_id, estado="mitigado", raw_json={"herramienta": "nuclei", "raw": {}}
    )

    def _lanza_error(target):
        raise ToolNotInstalledError("nuclei", "instalar nuclei")

    monkeypatch.setattr(api_main, "run_nuclei", _lanza_error)

    resp = client.post(
        f"/findings/{finding_id}/verificar-cierre", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 503
    assert _finding_row(finding_id)["estado"] == "mitigado"


# ---------------------------------------------------------------------------
# Herramientas sin endpoint HTTP re-escaneable: 422, nunca finge soporte
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("herramienta", ["semgrep", "trivy", "grype"])
def test_herramienta_sin_endpoint_reescaneable_422(client, herramienta):
    token, tenant_id = _registrar_tenant(client, f"Tenant Verif {herramienta}", f"verif-{herramienta}@verif.test")
    finding_id = _sembrar_finding(
        tenant_id, estado="mitigado", raw_json={"herramienta": herramienta, "raw": {}}
    )

    resp = client.post(
        f"/findings/{finding_id}/verificar-cierre", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 422
    assert _finding_row(finding_id)["estado"] == "mitigado"


# ---------------------------------------------------------------------------
# Estado terminal: no-op, nunca vuelve a escanear
# ---------------------------------------------------------------------------


def test_finding_ya_cerrado_no_reescanea(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Verif F", "veriff@verif.test")
    finding_id = _sembrar_finding(tenant_id, estado="cerrado", raw_json={"herramienta": "nuclei"})

    resp = client.post(
        f"/findings/{finding_id}/verificar-cierre", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["estado"] == "cerrado"
    assert data["reaparece"] is False
    assert _transiciones_de(finding_id) == []


# ---------------------------------------------------------------------------
# Scoping por tenant: 404, no 403
# ---------------------------------------------------------------------------


def test_finding_de_otro_tenant_404_no_403(client):
    token_dueno, tenant_dueno = _registrar_tenant(client, "Tenant Verif G Dueno", "duenog@verif.test")
    token_intruso, _ = _registrar_tenant(client, "Tenant Verif G Intruso", "intrusog@verif.test")
    finding_id = _sembrar_finding(tenant_dueno, estado="mitigado")

    resp = client.post(
        f"/findings/{finding_id}/verificar-cierre", headers={"Authorization": f"Bearer {token_intruso}"}
    )
    assert resp.status_code == 404


def test_finding_inexistente_404(client):
    token, _tenant_id = _registrar_tenant(client, "Tenant Verif Inexistente", "verif-inexistente@verif.test")
    resp = client.post(
        f"/findings/{uuid.uuid4()}/verificar-cierre", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404


def test_sin_token_401_o_403(client):
    resp = client.post(f"/findings/{uuid.uuid4()}/verificar-cierre")
    assert resp.status_code in (401, 403)
