"""Fase 1 (openspec/changes/fase1-deteccion-visible) -- `GET /findings/{id}`.

Cubre: detalle curado para el propio tenant, scoping por tenant (404 para
finding ajeno o inexistente -- nunca 403, para no confirmar la existencia
del recurso a quien no es su dueño), autenticación, tolerancia a
`raw_json` "viejo" (findings insertados antes de esta fase, sin las claves
de verificación), y la lista completa de evidencia Exploit-DB cuando
aplica.

Sigue el mismo patrón de siembra directa en `findings` (bypassando el
grafo LangGraph) que ya usa `test_multi_tenant_isolation.py`.
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
    return resp.json()["access_token"]


def _tenant_id_de(client, token: str) -> str:
    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    return resp.json()["tenant_id"]


def _sembrar_finding(
    tenant_id: str,
    raw_json: dict,
    *,
    tipo: str = "desconocido",
    severidad: str = "high",
    endpoint: str = "http://x.test/",
    confirmado: int = 0,
) -> str:
    conn = get_conn()
    try:
        scan_id = str(uuid.uuid4())
        finding_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado, trace_log_json) "
            "VALUES (?, ?, ?, 0, 'completado', '[]')",
            (scan_id, tenant_id, endpoint),
        )
        conn.execute(
            "INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (finding_id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, json.dumps(raw_json)),
        )
        conn.commit()
    finally:
        conn.close()
    return finding_id


def test_detalle_finding_propio_tenant_200_con_campos_curados(client):
    token = _registrar_tenant(client, "Tenant Detalle A", "detallea@finding.test")
    tenant_id = _tenant_id_de(client, token)

    raw = {
        "herramienta": "zap-baseline",
        "objetivo": "http://x.test",
        "raw": {
            "description": "SQL Injection detectada en el parámetro q.",
            "solution": "Usar consultas parametrizadas / prepared statements.",
            "reference": "https://owasp.org/www-community/attacks/SQL_Injection",
            "cweid": "89",
        },
        "confirmado": True,
        "estado_verificacion": "confirmado_via_exploitdb",
        "cves_detectados": ["CVE-2021-1234"],
        "evidencia_exploitdb": [
            {"cve": "CVE-2021-1234", "edb_id": "12345", "descripcion": "PoC pública real"}
        ],
    }
    finding_id = _sembrar_finding(tenant_id, raw, confirmado=1)

    resp = client.get(f"/findings/{finding_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["id"] == finding_id
    assert data["descripcion"] == "SQL Injection detectada en el parámetro q."
    assert data["solucion"] == "Usar consultas parametrizadas / prepared statements."
    assert data["referencia"] == "https://owasp.org/www-community/attacks/SQL_Injection"
    assert data["cwe"] == "89"
    assert data["estado_verificacion"] == "confirmado_via_exploitdb"
    assert data["confirmado"] is True
    assert data["cves_detectados"] == ["CVE-2021-1234"]
    # El contrato HTTP nunca expone raw_json crudo como campo de nivel superior.
    assert "raw_json" not in data


def test_detalle_finding_de_otro_tenant_404_no_403(client):
    token_dueno = _registrar_tenant(client, "Tenant Detalle Dueno", "dueno@finding.test")
    token_intruso = _registrar_tenant(client, "Tenant Detalle Intruso", "intruso@finding.test")
    tenant_dueno = _tenant_id_de(client, token_dueno)

    finding_id = _sembrar_finding(tenant_dueno, {"herramienta": "nuclei", "raw": {}})

    resp = client.get(f"/findings/{finding_id}", headers={"Authorization": f"Bearer {token_intruso}"})
    assert resp.status_code == 404


def test_detalle_finding_id_inexistente_404(client):
    token = _registrar_tenant(client, "Tenant Detalle Inexistente", "inexistente@finding.test")
    resp = client.get(f"/findings/{uuid.uuid4()}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_detalle_finding_sin_token_401_o_403(client):
    """Sin header Authorization en absoluto: `HTTPBearer()` por defecto
    devuelve 403 antes de llegar a `get_current_user` -- mismo criterio ya
    aceptado por `test_auth_flow.py::test_me_sin_token_devuelve_401_o_403`."""
    resp = client.get(f"/findings/{uuid.uuid4()}")
    assert resp.status_code in (401, 403)


def test_detalle_finding_con_token_invalido_401(client):
    resp = client.get(
        f"/findings/{uuid.uuid4()}", headers={"Authorization": "Bearer token-invalido-no-es-jwt"}
    )
    assert resp.status_code == 401


def test_detalle_finding_raw_json_viejo_sirve_200_con_defaults_vacios(client):
    """Un finding insertado ANTES de esta fase (raw_json = alerta cruda de
    ZAP, sin `herramienta`/`confirmado`/`estado_verificacion`) debe servir
    200 con defaults vacíos -- nunca 500."""
    token = _registrar_tenant(client, "Tenant Detalle Legacy", "legacy@finding.test")
    tenant_id = _tenant_id_de(client, token)

    raw_viejo = {"name": "Timestamp Disclosure - Unix", "risk": "Low", "url": "http://x.test/styles.css"}
    finding_id = _sembrar_finding(tenant_id, raw_viejo)

    resp = client.get(f"/findings/{finding_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["estado_verificacion"] is None
    assert data["cves_detectados"] == []
    assert data["evidencia_exploitdb"] == []
    assert data["cwe"] is None
    assert data["descripcion"] == ""
    assert data["solucion"] == ""


def test_detalle_finding_raw_json_vacio_o_corrupto_sirve_200(client):
    """`raw_json` NULL/"" o JSON corrupto (dato inconsistente, no debería
    pasar en la práctica pero el parser debe degradar con gracia) también
    sirve 200 con defaults, nunca 500."""
    token = _registrar_tenant(client, "Tenant Detalle Corrupto", "corrupto@finding.test")
    tenant_id = _tenant_id_de(client, token)

    conn = get_conn()
    try:
        scan_id = str(uuid.uuid4())
        finding_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado, trace_log_json) "
            "VALUES (?, ?, ?, 0, 'completado', '[]')",
            (scan_id, tenant_id, "http://corrupto.test/"),
        )
        conn.execute(
            "INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json) "
            "VALUES (?, ?, ?, 'desconocido', 'low', 'http://corrupto.test/', 0, ?)",
            (finding_id, scan_id, tenant_id, "{esto no es json valido"),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get(f"/findings/{finding_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["estado_verificacion"] is None
    assert data["descripcion"] == ""


def test_detalle_finding_con_evidencia_exploitdb_incluye_lista_completa(client):
    token = _registrar_tenant(client, "Tenant Detalle Evidencia", "evidencia@finding.test")
    tenant_id = _tenant_id_de(client, token)

    raw = {
        "herramienta": "zap-baseline",
        "raw": {
            "description": "RCE real",
            "solution": "Actualizar el componente afectado",
            "reference": "https://x.test/advisory",
            "cweid": "94",
        },
        "confirmado": True,
        "estado_verificacion": "confirmado_via_exploitdb",
        "cves_detectados": ["CVE-2021-1", "CVE-2021-2"],
        "evidencia_exploitdb": [
            {"cve": "CVE-2021-1", "edb_id": "1", "descripcion": "PoC uno"},
            {"cve": "CVE-2021-2", "edb_id": "2", "descripcion": "PoC dos"},
        ],
    }
    finding_id = _sembrar_finding(tenant_id, raw, confirmado=1)

    resp = client.get(f"/findings/{finding_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["evidencia_exploitdb"]) == 2
    assert {e["edb_id"] for e in data["evidencia_exploitdb"]} == {"1", "2"}
    assert all("descripcion" in e and "cve" in e for e in data["evidencia_exploitdb"])


def test_detalle_finding_sin_evidencia_devuelve_lista_vacia(client):
    token = _registrar_tenant(client, "Tenant Detalle Sin Evidencia", "sinevidencia@finding.test")
    tenant_id = _tenant_id_de(client, token)

    raw = {
        "herramienta": "zap-baseline",
        "raw": {"description": "CSP ausente"},
        "confirmado": False,
        "estado_verificacion": "no_verificable_sin_cve",
        "cves_detectados": [],
        "evidencia_exploitdb": [],
    }
    finding_id = _sembrar_finding(tenant_id, raw)

    resp = client.get(f"/findings/{finding_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["evidencia_exploitdb"] == []


# ---------------------------------------------------------------------------
# Dispatch por herramienta (nuclei/semgrep/trivy/grype) -- Task 0.1/0.2
# ---------------------------------------------------------------------------


def test_detalle_finding_nuclei_mapea_info_description_y_classification(client):
    token = _registrar_tenant(client, "Tenant Detalle Nuclei", "nuclei@finding.test")
    tenant_id = _tenant_id_de(client, token)

    raw = {
        "objetivo": "http://x.test",
        "herramienta": "nuclei",
        "raw": {
            "template-id": "swagger-api",
            "info": {
                "name": "Public Swagger API - Detect",
                "severity": "info",
                "description": "Se detectó una interfaz Swagger expuesta públicamente.",
                "reference": ["https://swagger.io/docs/"],
                "classification": {"cwe-id": ["CWE-200"]},
            },
            "host": "127.0.0.1",
        },
        "confirmado": False,
        "estado_verificacion": "no_verificable_sin_cve",
        "cves_detectados": [],
        "evidencia_exploitdb": [],
    }
    finding_id = _sembrar_finding(tenant_id, raw, tipo="Public Swagger API - Detect", severidad="info")

    resp = client.get(f"/findings/{finding_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["descripcion"] == "Se detectó una interfaz Swagger expuesta públicamente."
    assert data["referencia"] == "https://swagger.io/docs/"
    assert data["cwe"] == "CWE-200"
    assert data["solucion"] == ""  # nuclei no trae campo de solución dedicado


def test_detalle_finding_semgrep_mapea_extra_message_y_metadata(client):
    token = _registrar_tenant(client, "Tenant Detalle Semgrep", "semgrep@finding.test")
    tenant_id = _tenant_id_de(client, token)

    raw = {
        "objetivo": ".",
        "herramienta": "semgrep",
        "raw": {
            "check_id": "dockerfile.security.missing-user.missing-user",
            "path": "Dockerfile",
            "extra": {
                "severity": "ERROR",
                "message": "El Dockerfile no define un usuario no-root.",
                "metadata": {
                    "references": ["https://semgrep.dev/r/dockerfile.security.missing-user"],
                    "cwe": ["CWE-250: Execution with Unnecessary Privileges"],
                    "fix": "Agregar 'USER app' antes del ENTRYPOINT.",
                },
            },
        },
        "confirmado": False,
        "estado_verificacion": "no_verificable_sin_cve",
        "cves_detectados": [],
        "evidencia_exploitdb": [],
    }
    finding_id = _sembrar_finding(tenant_id, raw, tipo="missing-user", severidad="high")

    resp = client.get(f"/findings/{finding_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["descripcion"] == "El Dockerfile no define un usuario no-root."
    assert data["referencia"] == "https://semgrep.dev/r/dockerfile.security.missing-user"
    assert data["cwe"] == "CWE-250: Execution with Unnecessary Privileges"
    assert data["solucion"] == "Agregar 'USER app' antes del ENTRYPOINT."


def test_detalle_finding_trivy_mapea_description_references_fixedversion(client):
    token = _registrar_tenant(client, "Tenant Detalle Trivy", "trivy@finding.test")
    tenant_id = _tenant_id_de(client, token)

    raw = {
        "objetivo": "node:14",
        "herramienta": "trivy",
        "raw": {
            "VulnerabilityID": "CVE-2024-32002",
            "PkgName": "git",
            "Title": "git: RCE via crafted repository",
            "Description": "Clonar un repositorio malicioso puede ejecutar código arbitrario.",
            "Severity": "CRITICAL",
            "References": ["https://nvd.nist.gov/vuln/detail/CVE-2024-32002"],
            "CweIDs": ["CWE-94"],
            "FixedVersion": "2.45.1",
        },
        "confirmado": False,
        "estado_verificacion": "no_confirmado_cve_sin_poc_publica",
        "cves_detectados": ["CVE-2024-32002"],
        "evidencia_exploitdb": [],
    }
    finding_id = _sembrar_finding(tenant_id, raw, tipo="CVE-2024-32002", severidad="critical")

    resp = client.get(f"/findings/{finding_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["descripcion"] == "Clonar un repositorio malicioso puede ejecutar código arbitrario."
    assert data["referencia"] == "https://nvd.nist.gov/vuln/detail/CVE-2024-32002"
    assert data["cwe"] == "CWE-94"
    assert data["solucion"] == "Actualizar a la versión corregida: 2.45.1"


def test_detalle_finding_grype_mapea_vulnerability_description_urls_fix(client):
    token = _registrar_tenant(client, "Tenant Detalle Grype", "grype@finding.test")
    tenant_id = _tenant_id_de(client, token)

    raw = {
        "objetivo": "docker:node:14",
        "herramienta": "grype",
        "raw": {
            "vulnerability": {
                "id": "CVE-2023-4863",
                "severity": "High",
                "description": "Desbordamiento de buffer en libwebp.",
                "urls": ["https://nvd.nist.gov/vuln/detail/CVE-2023-4863"],
                "fix": {"versions": ["1.3.2"], "state": "fixed"},
            }
        },
        "confirmado": False,
        "estado_verificacion": "no_confirmado_cve_sin_poc_publica",
        "cves_detectados": ["CVE-2023-4863"],
        "evidencia_exploitdb": [],
    }
    finding_id = _sembrar_finding(tenant_id, raw, tipo="CVE-2023-4863", severidad="high")

    resp = client.get(f"/findings/{finding_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["descripcion"] == "Desbordamiento de buffer en libwebp."
    assert data["referencia"] == "https://nvd.nist.gov/vuln/detail/CVE-2023-4863"
    assert data["solucion"] == "Actualizar a: 1.3.2"
    # Grype no expone CWE en su schema de match -- no se inventa.
    assert data["cwe"] is None
