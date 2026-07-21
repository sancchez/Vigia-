"""Tests de la capa de selección de proveedor de LLM (`agents/_llm.py`).

Contexto (ver `eval/live_run_report.md`, Corrida 19, y `docs/despliegue.md`):
el proyecto solo soportaba Anthropic (API real o CLI de Claude Code como
fallback de desarrollo). Esta sesión agregó OpenAI como alternativa
genuinamente barata para producción, seleccionable vía `VIGIA_LLM_PROVIDER`
(explícito) o auto-detectada según qué API key esté presente.

Sigue el mismo patrón que `tests/test_llm_cli_encoding.py`: nada de red real
-- todo mockeado con `monkeypatch`. La fixture autouse `_sin_llm_real_por_defecto`
(`tests/conftest.py`) ya limpia `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/
`VIGIA_LLM_PROVIDER` antes de cada test, así que el punto de partida de
cada test de este archivo es "nada configurado" salvo que el propio test
lo setee explícitamente.

**Importante:** `_client_openai` (como `_client` para Anthropic) usa
`@lru_cache` -- se limpia explícitamente en cada test que la ejercita para
que un `ChatOpenAI` (real o fake) de un test no se filtre al siguiente.
"""

from __future__ import annotations

import pytest

from agents._llm import (
    DEFAULT_MODEL,
    DEFAULT_OPENAI_MODEL,
    LLMNoDisponibleError,
    _client_openai,
    _resolve_provider,
    call_claude,
)


@pytest.fixture(autouse=True)
def _limpiar_cache_cliente_openai():
    """Evita que un cliente OpenAI (fake) de un test se filtre a otro."""
    _client_openai.cache_clear()
    yield
    _client_openai.cache_clear()


# ---------------------------------------------------------------------------
# _resolve_provider(): auto-detección y override explícito
# ---------------------------------------------------------------------------


def test_resolve_provider_sin_nada_configurado_devuelve_anthropic():
    """Comportamiento por default IDÉNTICO al de antes de esta feature:
    nada configurado -> "anthropic" (que luego cae al fallback de CLI en
    `call_claude`, ver test más abajo)."""
    assert _resolve_provider() == "anthropic"


def test_resolve_provider_autodetecta_anthropic_si_solo_esa_key_esta(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    assert _resolve_provider() == "anthropic"


def test_resolve_provider_autodetecta_openai_si_solo_esa_key_esta(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    assert _resolve_provider() == "openai"


def test_resolve_provider_prefiere_anthropic_si_ambas_keys_estan(monkeypatch):
    """No romper el default de nadie que ya tenía ANTHROPIC_API_KEY
    configurada antes de que OPENAI_API_KEY también empezara a estarlo."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    assert _resolve_provider() == "anthropic"


def test_resolve_provider_explicito_gana_aunque_la_otra_key_este_presente(monkeypatch):
    """VIGIA_LLM_PROVIDER fuerza el proveedor incluso si la key "preferida"
    por auto-detección también está configurada."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("VIGIA_LLM_PROVIDER", "openai")
    assert _resolve_provider() == "openai"


def test_resolve_provider_explicito_no_sensible_a_mayusculas_ni_espacios(monkeypatch):
    monkeypatch.setenv("VIGIA_LLM_PROVIDER", "  OpenAI  ")
    assert _resolve_provider() == "openai"


def test_resolve_provider_rechaza_valor_invalido(monkeypatch):
    monkeypatch.setenv("VIGIA_LLM_PROVIDER", "gemini")
    with pytest.raises(LLMNoDisponibleError, match="gemini"):
        _resolve_provider()


# ---------------------------------------------------------------------------
# call_claude(): que el routing real llegue al backend correcto
# ---------------------------------------------------------------------------


def test_call_claude_sin_nada_configurado_sigue_cayendo_al_fallback_de_cli(monkeypatch):
    """Regresión explícita del requisito "no romper el default de hoy":
    con el mismo estado que tenía el proyecto ANTES de esta feature (sin
    ANTHROPIC_API_KEY, sin CLI de Claude disponible -- ya forzado por la
    fixture autouse), `call_claude` debe seguir fallando exactamente igual
    que siempre (CLI no encontrada), no intentar OpenAI."""
    with pytest.raises(LLMNoDisponibleError, match="claude"):
        call_claude("system", "user")


def test_call_claude_provider_openai_sin_key_falla_explicito_sin_fallback_de_cli(monkeypatch):
    """A diferencia de Anthropic, OpenAI no tiene un fallback de CLI -- si
    se pide explícitamente y no hay key, debe fallar claro, no silencioso
    ni intentando la CLI de Claude."""
    monkeypatch.setenv("VIGIA_LLM_PROVIDER", "openai")
    with pytest.raises(LLMNoDisponibleError, match="OPENAI_API_KEY"):
        call_claude("system", "user")


class _FakeOpenAIResponse:
    def __init__(self, text: str):
        self.content = text


class _FakeChatOpenAI:
    """Reemplaza a `ChatOpenAI` real -- captura los args de construcción y
    los mensajes de `.invoke()` para poder aserear sobre ellos, sin tocar
    la red."""

    ultima_instancia: "_FakeChatOpenAI | None" = None

    def __init__(self, *, model, api_key, timeout, max_retries):
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.mensajes_recibidos = None
        _FakeChatOpenAI.ultima_instancia = self

    def invoke(self, messages):
        self.mensajes_recibidos = messages
        # Texto en español con tildes/eñe -- ver la nota de encoding en
        # test_llm_cli_encoding.py: aquí no hay subprocess/decodificación
        # manual de por medio (langchain_core maneja los objetos de mensaje
        # directamente en memoria), pero igual vale confirmar que ningún
        # paso intermedio de este módulo corrompe el texto.
        return _FakeOpenAIResponse(
            "Hallazgo de seguridad: contraseña en texto plano — corrección: "
            "usar variables de entorno. Año: 2026. Sesión sin expiración también aplica."
        )


def test_call_claude_con_provider_openai_usa_el_modelo_default_de_openai(monkeypatch):
    """Si el llamador no pidió un `model` explícito (ningún call site real
    lo hace hoy -- ver agents/priorizacion.py, remediacion.py, etc.),
    `call_claude` debe usar DEFAULT_OPENAI_MODEL y NO el id de Claude que
    trae DEFAULT_MODEL como default de la firma de la función."""
    monkeypatch.setenv("VIGIA_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr("agents._llm.ChatOpenAI", _FakeChatOpenAI)

    resultado = call_claude("system prompt", "user message")

    fake = _FakeChatOpenAI.ultima_instancia
    assert fake is not None
    assert fake.model == DEFAULT_OPENAI_MODEL
    assert fake.model != DEFAULT_MODEL
    assert "contraseña" in resultado
    assert "ñ" in resultado
    assert "—" in resultado
    assert "Ã" not in resultado  # firma típica de mojibake utf-8-como-cp1252


def test_call_claude_con_provider_openai_respeta_un_model_explicito(monkeypatch):
    """Si alguien SÍ pasa `model=` explícito, se respeta tal cual en vez de
    sustituirlo por el default de OpenAI."""
    monkeypatch.setenv("VIGIA_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr("agents._llm.ChatOpenAI", _FakeChatOpenAI)

    call_claude("system", "user", model="gpt-5.4-nano")

    assert _FakeChatOpenAI.ultima_instancia.model == "gpt-5.4-nano"


def test_call_claude_con_provider_openai_pasa_system_y_user_message_correctos(monkeypatch):
    monkeypatch.setenv("VIGIA_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr("agents._llm.ChatOpenAI", _FakeChatOpenAI)

    call_claude("eres un asistente de seguridad", "hallazgo: XSS reflejado")

    mensajes = _FakeChatOpenAI.ultima_instancia.mensajes_recibidos
    assert len(mensajes) == 2
    assert mensajes[0].content == "eres un asistente de seguridad"
    assert mensajes[1].content == "hallazgo: XSS reflejado"


def test_call_claude_con_provider_openai_maneja_contenido_estructurado_en_bloques(monkeypatch):
    """Igual que `_call_via_api` para Anthropic: si `.content` viene como
    una lista de bloques (algunos backends de langchain lo hacen), se
    concatena el texto de cada bloque en vez de devolver la lista cruda."""

    class _FakeChatOpenAIBloques(_FakeChatOpenAI):
        def invoke(self, messages):
            self.mensajes_recibidos = messages
            return _FakeOpenAIResponse(
                [
                    {"type": "text", "text": "primera parte, "},
                    {"type": "text", "text": "segunda parte"},
                ]
            )

    monkeypatch.setenv("VIGIA_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr("agents._llm.ChatOpenAI", _FakeChatOpenAIBloques)

    resultado = call_claude("system", "user")

    assert resultado == "primera parte, \nsegunda parte"


def test_call_claude_provider_invalido_no_intenta_ningun_backend(monkeypatch):
    monkeypatch.setenv("VIGIA_LLM_PROVIDER", "not-a-real-provider")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    with pytest.raises(LLMNoDisponibleError, match="not-a-real-provider"):
        call_claude("system", "user")
