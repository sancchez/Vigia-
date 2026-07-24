"""Fase 1 (openspec/changes/fase1-deteccion-visible) -- verificación
determinista aplicada al escaneo activo.

Dos niveles de prueba, mismo patrón que `test_scan_activo_asset_gate.py`:

1. Unidad pura sobre `agents/verificacion.py::verificar_un_hallazgo` (el
   wrapper público nuevo) -- confirma que produce los 3 valores posibles de
   `estado_verificacion` sin pasar por HTTP ni por el grafo.
2. Integración sobre `POST /scan/activo` real vía `TestClient` -- confirma
   que `api/main.py::_correr_escaneo_activo_en_background` YA NO inserta
   `confirmado=0` fijo: cada alerta pasa por `verificar_un_hallazgo` antes
   del `INSERT INTO findings`, y el resultado (`confirmado` real +
   `estado_verificacion` dentro de `raw_json`) se persiste por fila.

`find_exploits_for_cve` (el índice CSV real de Exploit-DB,
`tools/exploit_intel.py`) se mockea en `agents.verificacion` -- mismo
principio que el resto de la suite: determinista y rápido, sin depender de
que el CSV vendorizado exista en la máquina que corre los tests.
"""

from __future__ import annotations

import json
import time

import agents.verificacion as verificacion_module
import api.main as main_module
from agents.verificacion import verificar_un_hallazgo
from db.connection import get_conn
from tools.exploit_intel import ExploitRecord


def _mockear_indice_exploitdb(monkeypatch, mapping: dict[str, list[ExploitRecord]]):
    """Reemplaza `find_exploits_for_cve` dentro de `agents.verificacion` por
    una búsqueda en memoria sobre `mapping` (clave: CVE en mayúsculas)."""

    def _fake(cve_id: str, limit: int = 20):
        return mapping.get(cve_id.upper(), [])

    monkeypatch.setattr(verificacion_module, "find_exploits_for_cve", _fake)


# ---------------------------------------------------------------------------
# 1.2-1.4 -- unidad: verificar_un_hallazgo (wrapper público) sobre los 3 casos
# ---------------------------------------------------------------------------


def test_verificar_un_hallazgo_con_cve_confirmable_via_exploitdb(monkeypatch):
    _mockear_indice_exploitdb(
        monkeypatch,
        {
            "CVE-2021-44228": [
                ExploitRecord(
                    edb_id="50592",
                    file="multiple/webapps/50592.py",
                    description="Apache Log4j2 - Remote Code Execution (RCE)",
                    date_published="2021-12-13",
                    author="someone",
                    type="webapps",
                    platform="multiple",
                    cves=["CVE-2021-44228"],
                )
            ]
        },
    )
    alerta = {
        "name": "Log4Shell",
        "risk": "High",
        "description": "El servicio es vulnerable a CVE-2021-44228 (Log4Shell).",
    }

    resultado = verificar_un_hallazgo(alerta)

    assert resultado["confirmado"] is True
    assert resultado["estado_verificacion"] == "confirmado_via_exploitdb"
    assert resultado["cves_detectados"] == ["CVE-2021-44228"]
    assert resultado["evidencia_exploitdb"] == [
        {
            "cve": "CVE-2021-44228",
            "edb_id": "50592",
            "descripcion": "Apache Log4j2 - Remote Code Execution (RCE)",
        }
    ]
    # El wrapper no debe perder ninguna clave original del hallazgo.
    assert resultado["name"] == "Log4Shell"


def test_verificar_un_hallazgo_sin_cve_en_su_texto_no_verificable():
    """Hallazgo de configuración (CSP, clickjacking, headers) sin ningún CVE
    -- MUST terminar en no_verificable_sin_cve, no como fallo del sistema."""
    alerta = {
        "name": "Content Security Policy (CSP) Header Not Set",
        "risk": "Medium",
        "description": "El header Content-Security-Policy no está presente en la respuesta.",
    }

    resultado = verificar_un_hallazgo(alerta)

    assert resultado["confirmado"] is False
    assert resultado["estado_verificacion"] == "no_verificable_sin_cve"
    assert resultado["cves_detectados"] == []
    assert resultado["evidencia_exploitdb"] == []


def test_verificar_un_hallazgo_con_cve_real_sin_poc_publica(monkeypatch):
    """CVE real referenciado, pero sin entrada en el índice local de
    Exploit-DB -- no_confirmado_cve_sin_poc_publica, no confirmado a ciegas."""
    _mockear_indice_exploitdb(monkeypatch, {})  # índice vacío: ningún CVE tiene PoC

    alerta = {
        "name": "Vulnerabilidad conocida sin exploit público",
        "risk": "Medium",
        "description": "Referencia CVE-2099-99999 sin PoC pública indexada.",
    }

    resultado = verificar_un_hallazgo(alerta)

    assert resultado["confirmado"] is False
    assert resultado["estado_verificacion"] == "no_confirmado_cve_sin_poc_publica"
    assert resultado["cves_detectados"] == ["CVE-2099-99999"]
    assert resultado["evidencia_exploitdb"] == []


# ---------------------------------------------------------------------------
# 2.3 -- integración: POST /scan/activo persiste confirmado real por fila
# ---------------------------------------------------------------------------


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123"):
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _esperar_scan_terminado(client, token: str, scan_id: str, intentos: int = 100, espera: float = 0.05) -> dict:
    for _ in range(intentos):
        resp = client.get(f"/scans/{scan_id}", headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 200 and resp.json()["estado"] != "corriendo":
            return resp.json()
        time.sleep(espera)
    raise AssertionError(f"scan {scan_id} no terminó dentro del tiempo esperado (sigue 'corriendo')")


def test_escaneo_activo_persiste_confirmado_real_no_cero_fijo(client, monkeypatch):
    """El caso concreto que motiva esta fase: antes de este cambio, TODA
    alerta de escaneo activo se insertaba con confirmado=0 fijo (línea
    hardcodeada del INSERT). Ahora cada alerta pasa por
    `verificar_un_hallazgo` antes de insertarse."""
    _mockear_indice_exploitdb(
        monkeypatch,
        {
            "CVE-2017-5638": [
                ExploitRecord(
                    edb_id="41570",
                    file="multiple/remote/41570.py",
                    description="Apache Struts 2 - Content-Type RCE",
                    date_published="2017-03-07",
                    author="someone",
                    type="remote",
                    platform="multiple",
                    cves=["CVE-2017-5638"],
                )
            ]
        },
    )

    class _ResultadoZapFalso:
        findings = [
            {
                "name": "Apache Struts2 Content-Type RCE",
                "risk": "High",
                "description": "Vulnerable a CVE-2017-5638, ejecución remota de código.",
                "url": "http://vulnerable-real.test/upload",
            },
            {
                "name": "Content Security Policy (CSP) Header Not Set",
                "risk": "Medium",
                "url": "http://vulnerable-real.test/",
            },
        ]
        detalle_final = "mock: 2 hallazgos"

    monkeypatch.setattr(main_module, "run_checkpointed_active_scan", lambda *_a, **_k: _ResultadoZapFalso())
    monkeypatch.setattr(main_module, "verificar_asset", lambda *_a, **_k: (True, "mock: token encontrado"))

    token = _registrar_tenant(client, "Escaneo Con Verificacion", "verif@escaneoactivo.test")

    asset = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token}"},
        json={"tipo": "dominio", "valor": "vulnerable-real.test"},
    )
    assert asset.status_code == 200
    asset_id = asset.json()["id"]
    verificacion = client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token}"},
        json={"metodo": "dns_txt"},
    )
    assert verificacion.status_code == 200

    resp = client.post(
        "/scan/activo",
        headers={"Authorization": f"Bearer {token}"},
        json={"target_url": "http://vulnerable-real.test", "autorizacion_firmada": True},
    )
    assert resp.status_code == 202
    scan_id = resp.json()["scan_id"]

    detalle = _esperar_scan_terminado(client, token, scan_id)
    assert detalle["estado"] == "completado"
    assert len(detalle["findings"]) == 2

    conn = get_conn()
    try:
        filas = conn.execute(
            "SELECT * FROM findings WHERE scan_id = ? ORDER BY tipo", (scan_id,)
        ).fetchall()
    finally:
        conn.close()

    por_tipo = {f["tipo"]: f for f in filas}
    struts = por_tipo["Apache Struts2 Content-Type RCE"]
    csp = por_tipo["Content Security Policy (CSP) Header Not Set"]

    assert bool(struts["confirmado"]) is True
    raw_struts = json.loads(struts["raw_json"])
    assert raw_struts["estado_verificacion"] == "confirmado_via_exploitdb"
    assert raw_struts["evidencia_exploitdb"][0]["edb_id"] == "41570"

    assert bool(csp["confirmado"]) is False
    raw_csp = json.loads(csp["raw_json"])
    assert raw_csp["estado_verificacion"] == "no_verificable_sin_cve"
