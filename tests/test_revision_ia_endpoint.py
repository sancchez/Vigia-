"""Fase 4 (openspec/changes/fase4-revision-codigo-ia) -- `POST /reports/revision-ia`.

Un caso por escenario de `specs/revision-ia/spec.md`: doble consentimiento
(403 con motivo distinto por cada flag faltante, sin leer archivos),
persistencia como `scans`+`findings` sintéticos (con y sin hallazgos),
degradación cuando el LLM no está disponible, y validación de traversal del
sandbox por tenant (unit, sin pasar por HTTP).

`agents.revision_ia.call_claude` se monkeypatchea directamente (mismo
principio que el resto de la suite -- ver `tests/conftest.py`,
`_sin_llm_real_por_defecto`): nunca se dispara una llamada real al LLM.
`revisar_codigo()` en sí NO se toca ni se re-testea aquí a fondo (ya está
probado en su propio módulo, ver docstring de `agents/revision_ia.py`) --
estos tests cubren la capa HTTP nueva: consentimiento, sandbox,
persistencia.
"""

from __future__ import annotations

import json

import pytest

import agents.revision_ia as revision_ia_module
from api.main import UPLOADS_ROOT, _map_severidad_revision, _nombre_archivo_seguro
from db.connection import get_conn
from fastapi import HTTPException


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123"):
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    tenant_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["tenant_id"]
    return token, tenant_id


def _post_revision(
    client,
    token: str,
    *,
    autorizacion_firmada: bool,
    envio_codigo_a_llm_autorizado: bool,
    archivos: list[tuple[str, bytes]] | None = None,
):
    archivos = archivos or [("modulo.py", b"def foo():\n    return 1\n")]
    return client.post(
        "/reports/revision-ia",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "autorizacion_firmada": str(autorizacion_firmada),
            "envio_codigo_a_llm_autorizado": str(envio_codigo_a_llm_autorizado),
            "contexto_negocio": "",
            "label": "test",
        },
        files=[("files", (nombre, contenido, "text/x-python")) for nombre, contenido in archivos],
    )


def _mockear_call_claude(monkeypatch, hallazgos: list[dict] | Exception):
    """Reemplaza `agents.revision_ia.call_claude` -- si `hallazgos` es una
    excepción, la levanta; si es una lista, la serializa como respuesta del
    LLM (mismo shape que `_parsear_hallazgos` espera)."""

    def _fake(system_prompt: str, entrada: str) -> str:
        if isinstance(hallazgos, Exception):
            raise hallazgos
        return json.dumps(hallazgos)

    monkeypatch.setattr(revision_ia_module, "call_claude", _fake)


# ---------------------------------------------------------------------------
# 4.1 -- doble consentimiento
# ---------------------------------------------------------------------------


def test_403_sin_autorizacion_firmada_y_ningun_archivo_leido(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Revision A", "revA@revision.test")

    resp = _post_revision(client, token, autorizacion_firmada=False, envio_codigo_a_llm_autorizado=True)

    assert resp.status_code == 403
    assert "autorizacion_firmada" in resp.json()["detail"]
    # Ningún archivo debió escribirse -- el sandbox del tenant nunca se creó.
    assert not (UPLOADS_ROOT / tenant_id).exists()


def test_403_sin_envio_llm_autorizado_motivo_distinto_y_ningun_archivo_leido(client):
    token, tenant_id = _registrar_tenant(client, "Tenant Revision B", "revB@revision.test")

    resp = _post_revision(client, token, autorizacion_firmada=True, envio_codigo_a_llm_autorizado=False)

    assert resp.status_code == 403
    assert "envio_codigo_a_llm_autorizado" in resp.json()["detail"]
    assert not (UPLOADS_ROOT / tenant_id).exists()


# ---------------------------------------------------------------------------
# 4.2 -- persistencia con hallazgos
# ---------------------------------------------------------------------------


def test_200_con_ambos_flags_persiste_scan_y_findings_y_aparecen_en_get_findings(client, monkeypatch):
    token, tenant_id = _registrar_tenant(client, "Tenant Revision C", "revC@revision.test")
    _mockear_call_claude(
        monkeypatch,
        [
            {
                "archivo": "modulo.py",
                "linea_aprox": 3,
                "categoria": "control_de_acceso",
                "severidad": "alta",
                "descripcion": "El chequeo de rol compara el campo equivocado.",
                "evidencia": "if user.role == 'admn':",
                "recomendacion": "Comparar contra 'admin'.",
                "relacionado_con_semgrep": None,
            },
            {
                "archivo": "modulo.py",
                "linea_aprox": 9,
                "categoria": "aislamiento_multi_tenant",
                "severidad": "media",
                "descripcion": "La consulta no filtra por tenant_id en todos los caminos.",
                "evidencia": "SELECT * FROM datos WHERE id = ?",
                "recomendacion": "Agregar AND tenant_id = ? a la consulta.",
                "relacionado_con_semgrep": None,
            },
        ],
    )

    resp = _post_revision(client, token, autorizacion_firmada=True, envio_codigo_a_llm_autorizado=True)

    assert resp.status_code == 200
    body = resp.json()
    assert body["scan_id"] is not None
    assert len(body["hallazgos_logica"]) == 2
    assert body["hallazgos_logica"][0]["severidad"] == "alta"

    conn = get_conn()
    try:
        scan = conn.execute("SELECT * FROM scans WHERE id = ?", (body["scan_id"],)).fetchone()
        assert scan is not None
        findings = conn.execute(
            "SELECT * FROM findings WHERE scan_id = ?", (body["scan_id"],)
        ).fetchall()
    finally:
        conn.close()
    assert len(findings) == 2
    severidades = sorted(dict(f)["severidad"] for f in findings)
    assert severidades == ["high", "medium"]

    resp_findings = client.get("/findings", headers={"Authorization": f"Bearer {token}"})
    assert resp_findings.status_code == 200
    ids_en_get = {f["id"] for f in resp_findings.json()}
    ids_creados = {dict(f)["id"] for f in findings}
    assert ids_creados.issubset(ids_en_get)

    # El sandbox de la review se borra al terminar (código propietario del
    # cliente, no se retiene).
    assert not (UPLOADS_ROOT / tenant_id).exists() or not any((UPLOADS_ROOT / tenant_id).iterdir())


# ---------------------------------------------------------------------------
# 4.3 -- sin hallazgos / LLM no disponible
# ---------------------------------------------------------------------------


def test_0_hallazgos_sin_filas_findings_y_scan_id_null(client, monkeypatch):
    token, _tenant_id = _registrar_tenant(client, "Tenant Revision D", "revD@revision.test")
    _mockear_call_claude(monkeypatch, [])

    resp = _post_revision(client, token, autorizacion_firmada=True, envio_codigo_a_llm_autorizado=True)

    assert resp.status_code == 200
    body = resp.json()
    assert body["scan_id"] is None
    assert body["hallazgos_logica"] == []


def test_llm_no_disponible_200_con_hallazgos_vacios(client, monkeypatch):
    from agents._llm import LLMNoDisponibleError

    token, _tenant_id = _registrar_tenant(client, "Tenant Revision E", "revE@revision.test")
    _mockear_call_claude(monkeypatch, LLMNoDisponibleError("LLM caído en el test"))

    resp = _post_revision(client, token, autorizacion_firmada=True, envio_codigo_a_llm_autorizado=True)

    assert resp.status_code == 200
    body = resp.json()
    assert body["hallazgos_logica"] == []
    assert body["scan_id"] is None


# ---------------------------------------------------------------------------
# 4.4 -- unit: traversal del sandbox + mapeo de severidad
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nombre_malicioso",
    [
        "../../etc/passwd",
        "..\\..\\config\\secrets.env",
        "..",
        "sub/dir/archivo.py",
    ],
)
def test_nombre_archivo_con_traversal_rechazado_sin_lectura(nombre_malicioso):
    with pytest.raises(HTTPException) as exc_info:
        _nombre_archivo_seguro(nombre_malicioso)
    assert exc_info.value.status_code == 422


def test_nombre_archivo_normal_aceptado():
    assert _nombre_archivo_seguro("modulo.py") == "modulo.py"


def test_map_severidad_revision():
    assert _map_severidad_revision("alta") == "high"
    assert _map_severidad_revision("media") == "medium"
    assert _map_severidad_revision("baja") == "low"
    assert _map_severidad_revision("ALTA") == "high"
    assert _map_severidad_revision("desconocida") == "info"
    assert _map_severidad_revision("") == "info"
