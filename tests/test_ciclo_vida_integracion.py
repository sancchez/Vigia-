"""Fase 2a (openspec/changes/fase2a-ciclo-vida-hallazgo) -- Task 5.2:
confirma con un test real (no solo manualmente) que `GET /findings` y
`GET /findings/{id}` devuelven los 6 campos nuevos de ciclo de vida con
valores coherentes tras un escaneo activo real (mockeado en la capa de ZAP,
igual que `test_scan_activo_asset_gate.py`), y que `scans.asset_id` queda
poblado (antes de este change, se descartaba)."""

from __future__ import annotations

import api.main as main_module
from db.connection import get_conn


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123"):
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _esperar_scan_terminado(client, token: str, scan_id: str, intentos: int = 100, espera: float = 0.05) -> str:
    import time

    for _ in range(intentos):
        resp = client.get(f"/scans/{scan_id}", headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 200 and resp.json()["estado"] != "corriendo":
            return resp.json()["estado"]
        time.sleep(espera)
    raise AssertionError(f"scan {scan_id} no terminó dentro del tiempo esperado")


def test_scan_activo_real_puebla_asset_id_y_campos_de_ciclo_de_vida(client, monkeypatch):
    llamado = {"zap": False}

    class _ResultadoFalso:
        findings: list = [
            {
                "name": "SQL Injection",
                "risk": "High",
                "url": "http://midominiociclo.test/rest/products/search",
                "instances": [{"uri": "http://midominiociclo.test/rest/products/search"}],
            }
        ]
        detalle_final: str = "mock: una alerta real de ejemplo"

    def _zap_espia(*_args, **_kwargs):
        llamado["zap"] = True
        return _ResultadoFalso()

    monkeypatch.setattr(main_module, "run_checkpointed_active_scan", _zap_espia)
    # Fase 8 (Grupo 3): el camino de escaneo activo ahora también corre
    # Nuclei -- se mockea igual que ZAP para que este test siga enfocado en
    # ciclo de vida/asset_id y no dependa de una ejecución real de Nuclei.
    class _ResultadoNucleiFalso:
        findings: list = []
        detalle_final: str = "mock: nuclei sin hallazgos"

    monkeypatch.setattr(main_module, "run_nuclei", lambda *_a, **_k: _ResultadoNucleiFalso())
    monkeypatch.setattr(
        main_module, "verificar_asset", lambda *_a, **_k: (True, "mock: token encontrado")
    )

    token = _registrar_tenant(client, "Pyme Ciclo Vida", "ciclovida@ciclo.test")
    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token}"},
        json={"tipo": "dominio", "valor": "midominiociclo.test"},
    )
    assert asset.status_code == 200
    asset_id = asset.json()["id"]

    verificacion = client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token}"},
        json={"metodo": "dns_txt"},
    )
    assert verificacion.json()["verificado"] is True

    resp = client.post(
        "/scan/activo",
        headers={"Authorization": f"Bearer {token}"},
        json={"target_url": "http://midominiociclo.test", "autorizacion_firmada": True},
    )
    assert resp.status_code == 202
    scan_id = resp.json()["scan_id"]

    estado_final = _esperar_scan_terminado(client, token, scan_id)
    assert estado_final == "completado"
    assert llamado["zap"] is True

    # scans.asset_id ya no se descarta (bug real cerrado en esta fase).
    conn = get_conn()
    try:
        fila_scan = dict(conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone())
    finally:
        conn.close()
    assert fila_scan["asset_id"] == asset_id

    listado = client.get("/findings", headers={"Authorization": f"Bearer {token}"})
    assert listado.status_code == 200
    hallazgos = listado.json()
    assert len(hallazgos) == 1
    hallazgo = hallazgos[0]
    assert hallazgo["estado"] == "descubierto"
    assert hallazgo["owner_user_id"] is None
    assert hallazgo["owner_email"] is None
    assert hallazgo["sla_due_at"] is not None  # severidad "high" -> SLA default 30 días
    assert hallazgo["primera_vez_visto"] is not None
    assert hallazgo["primera_vez_visto"] == hallazgo["ultima_vez_visto"]

    detalle = client.get(
        f"/findings/{hallazgo['id']}", headers={"Authorization": f"Bearer {token}"}
    )
    assert detalle.status_code == 200
    data = detalle.json()
    assert data["estado"] == "descubierto"
    assert data["sla_due_at"] == hallazgo["sla_due_at"]
    assert data["primera_vez_visto"] == hallazgo["primera_vez_visto"]
    assert data["ultima_vez_visto"] == hallazgo["ultima_vez_visto"]
