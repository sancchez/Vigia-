"""Registro de costo/uso de LLM (Fase 6a,
openspec/changes/fase6-endurecimiento-produccion) -- `agents/_llm.py`
(`set_llm_context`, `_registrar_uso`) y `GET /costos` (`api/main.py`).

Sigue el mismo patrón de mocking que `tests/test_llm_provider_selection.py`:
nada de red real, `ChatAnthropic`/`ChatOpenAI` sustituidos por fakes. La
fixture autouse `_sin_llm_real_por_defecto` (`tests/conftest.py`) sigue
limpiando `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`VIGIA_LLM_PROVIDER` antes de
cada test.

Sin `set_llm_context()` (o con tenant_id=None), ninguna llamada debe
escribir en `llm_usage` -- es el comportamiento por default que protege a
TODOS los demás tests de la suite (que no llaman `set_llm_context`) de
tocar la base de datos real por accidente.
"""

from __future__ import annotations

import uuid

import pytest

from agents._llm import (
    DEFAULT_MODEL,
    _client_openai,
    _llm_context,
    call_claude,
    set_llm_context,
)
from db.connection import get_conn


@pytest.fixture(autouse=True)
def _limpiar_cache_cliente_openai():
    _client_openai.cache_clear()
    yield
    _client_openai.cache_clear()


@pytest.fixture(autouse=True)
def _resetear_contexto_llm():
    """Evita que el contexto seteado por un test se filtre al siguiente
    (contextvars con default=None -- ver agents/_llm.py)."""
    token = _llm_context.set(None)
    yield
    _llm_context.reset(token)


def _crear_tenant(conn, tenant_id: str) -> None:
    conn.execute(
        "INSERT INTO tenants (id, slug, name) VALUES (?, ?, ?)",
        (tenant_id, tenant_id, f"Tenant {tenant_id}"),
    )


def _crear_scan(conn, tenant_id: str, scan_id: str) -> None:
    """`llm_usage.scan_id` es FK real a `scans(id)` -- un scan_id de prueba
    tiene que existir de verdad (foreign_keys=ON en cada conexión sqlite3,
    ver db/connection.py) para no violar la constraint."""
    conn.execute(
        "INSERT INTO scans (id, tenant_id, target) VALUES (?, ?, ?)",
        (scan_id, tenant_id, "target-de-prueba.test"),
    )


class _FakeUsageResponse:
    def __init__(self, text: str, input_tokens: int, output_tokens: int):
        self.content = text
        self.usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }


class _FakeChatAnthropic:
    ultima_instancia: "_FakeChatAnthropic | None" = None

    def __init__(self, *, model, api_key, timeout, max_retries):
        self.model = model
        _FakeChatAnthropic.ultima_instancia = self

    def invoke(self, messages):
        return _FakeUsageResponse("hallazgo simulado", input_tokens=1000, output_tokens=500)


class _FakeChatOpenAI:
    ultima_instancia: "_FakeChatOpenAI | None" = None

    def __init__(self, *, model, api_key, timeout, max_retries):
        self.model = model
        _FakeChatOpenAI.ultima_instancia = self

    def invoke(self, messages):
        return _FakeUsageResponse("hallazgo simulado openai", input_tokens=2000, output_tokens=1000)


# ---------------------------------------------------------------------------
# 1. Sin contexto poblado -- nunca escribe en llm_usage (default seguro)
# ---------------------------------------------------------------------------


def test_sin_set_llm_context_no_escribe_nada(test_db, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setattr("agents._llm.ChatAnthropic", _FakeChatAnthropic)

    call_claude("system", "user")

    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM llm_usage").fetchone()
        assert dict(total)["n"] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Con contexto -- Anthropic real (mockeado) registra tokens + costo
# ---------------------------------------------------------------------------


def test_call_via_api_anthropic_registra_uso_con_costo(test_db, monkeypatch):
    tenant_id = str(uuid.uuid4())
    scan_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        _crear_tenant(conn, tenant_id)
        _crear_scan(conn, tenant_id, scan_id)
        conn.commit()
    finally:
        conn.close()

    set_llm_context(tenant_id, scan_id)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setattr("agents._llm.ChatAnthropic", _FakeChatAnthropic)

    call_claude("system", "user")

    conn = get_conn()
    try:
        fila = dict(conn.execute("SELECT * FROM llm_usage WHERE tenant_id = ?", (tenant_id,)).fetchone())
    finally:
        conn.close()

    assert fila["scan_id"] == scan_id
    assert fila["provider"] == "anthropic"
    assert fila["model"] == DEFAULT_MODEL
    assert fila["tokens_in"] == 1000
    assert fila["tokens_out"] == 500
    # DEFAULT_MODEL tiene tarifa conocida ($3/$15 por millón, ver
    # agents/_llm.py::_TARIFAS_POR_MILLON_USD) -- costo real, no None.
    assert fila["costo_estimado"] == pytest.approx((1000 / 1_000_000) * 3.0 + (500 / 1_000_000) * 15.0)


def test_call_via_api_openai_registra_uso_con_costo(test_db, monkeypatch):
    tenant_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        _crear_tenant(conn, tenant_id)
        conn.commit()
    finally:
        conn.close()

    set_llm_context(tenant_id, None)
    monkeypatch.setenv("VIGIA_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr("agents._llm.ChatOpenAI", _FakeChatOpenAI)

    call_claude("system", "user")

    conn = get_conn()
    try:
        fila = dict(conn.execute("SELECT * FROM llm_usage WHERE tenant_id = ?", (tenant_id,)).fetchone())
    finally:
        conn.close()

    assert fila["scan_id"] is None
    assert fila["provider"] == "openai"
    assert fila["tokens_in"] == 2000
    assert fila["tokens_out"] == 1000
    assert fila["costo_estimado"] == pytest.approx((2000 / 1_000_000) * 0.75 + (1000 / 1_000_000) * 4.50)


# ---------------------------------------------------------------------------
# 3. CLI -- se registra la llamada pero costo_estimado es siempre None
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, stdout: str):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def test_call_via_cli_registra_uso_sin_costo(test_db, monkeypatch):
    tenant_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        _crear_tenant(conn, tenant_id)
        conn.commit()
    finally:
        conn.close()

    set_llm_context(tenant_id, None)
    # Fuerza el camino CLI: sin keys, con el binario "claude" encontrado.
    monkeypatch.setattr("agents._llm.shutil.which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        "agents._llm.subprocess.run", lambda *a, **k: _FakeCompletedProcess("respuesta cli")
    )

    resultado = call_claude("system", "user")

    assert resultado == "respuesta cli"
    conn = get_conn()
    try:
        fila = dict(conn.execute("SELECT * FROM llm_usage WHERE tenant_id = ?", (tenant_id,)).fetchone())
    finally:
        conn.close()

    assert fila["provider"] == "cli"
    assert fila["tokens_in"] is None
    assert fila["tokens_out"] is None
    assert fila["costo_estimado"] is None


# ---------------------------------------------------------------------------
# 4. GET /costos -- agregación por scan + aislamiento por tenant
# ---------------------------------------------------------------------------


def _registrar_tenant_http(client, nombre: str, email: str) -> tuple[str, str]:
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": "claveSegura123"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    me = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()
    return token, me["tenant_id"]


def _insertar_uso(tenant_id: str, scan_id, provider: str, tokens_in, tokens_out, costo):
    conn = get_conn()
    try:
        if scan_id is not None and not conn.execute("SELECT 1 FROM scans WHERE id = ?", (scan_id,)).fetchone():
            _crear_scan(conn, tenant_id, scan_id)
        conn.execute(
            """
            INSERT INTO llm_usage (id, tenant_id, scan_id, provider, model, tokens_in, tokens_out, costo_estimado)
            VALUES (?, ?, ?, ?, 'claude-sonnet-4-5-20250929', ?, ?, ?)
            """,
            (str(uuid.uuid4()), tenant_id, scan_id, provider, tokens_in, tokens_out, costo),
        )
        conn.commit()
    finally:
        conn.close()


def test_get_costos_agrega_por_scan_y_total(client):
    token, tenant_id = _registrar_tenant_http(client, "Pyme Costos", "costos@fase6a.test")
    scan_id = str(uuid.uuid4())
    _insertar_uso(tenant_id, scan_id, "anthropic", 1000, 500, 0.0105)
    _insertar_uso(tenant_id, scan_id, "anthropic", 2000, 1000, 0.021)
    _insertar_uso(tenant_id, None, "cli", None, None, None)

    resp = client.get("/costos", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_llamadas"] == 3
    assert body["total_costo_estimado"] == pytest.approx(0.0315)

    por_scan = {fila["scan_id"]: fila for fila in body["por_scan"]}
    assert por_scan[scan_id]["llamadas"] == 2
    assert por_scan[scan_id]["tokens_in"] == 3000
    assert por_scan[scan_id]["costo_estimado"] == pytest.approx(0.0315)
    assert por_scan[None]["llamadas"] == 1
    assert por_scan[None]["costo_estimado"] is None


def test_get_costos_aislado_por_tenant(client):
    token_a, tenant_a = _registrar_tenant_http(client, "Tenant A", "a@fase6a.test")
    token_b, tenant_b = _registrar_tenant_http(client, "Tenant B", "b@fase6a.test")
    _insertar_uso(tenant_a, None, "anthropic", 1000, 500, 0.0105)
    _insertar_uso(tenant_b, None, "anthropic", 9000, 9000, 99.0)

    resp_a = client.get("/costos", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_a.json()["total_llamadas"] == 1
    assert resp_a.json()["total_costo_estimado"] == pytest.approx(0.0105)

    resp_b = client.get("/costos", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.json()["total_llamadas"] == 1
    assert resp_b.json()["total_costo_estimado"] == pytest.approx(99.0)
