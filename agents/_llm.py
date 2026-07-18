"""Helper compartido para los agentes que razonan con Claude (sección 7 del plan).

No es un agente en sí — es utilidad común, igual que `tools/_shared.py` lo
es para los wrappers de herramientas. El agente de Verificación
(`agents/verificacion.py`) NO importa este módulo por diseño: su lógica es
determinista y no depende del LLM (sección 8.1, "separado del razonamiento
de la IA").

Dos backends, mismo contrato (`call_claude`):
1. **API directa** (`langchain_anthropic.ChatAnthropic`) si `ANTHROPIC_API_KEY`
   está configurada — el camino de producción real (facturación propia,
   sin depender de que la máquina tenga la CLI instalada y con sesión iniciada).
2. **CLI de Claude Code** (`claude -p`) si no hay API key pero el binario
   `claude` está disponible en PATH — reusa la sesión ya autenticada de
   quien esté corriendo el proyecto en su máquina, sin pagar ni configurar
   nada aparte. Pensado para desarrollo/demo, no para el servicio en
   producción con múltiples tenants (ahí sí hace falta la key real).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

# Configurable por entorno para no atar el código a un solo model id
# a medida que Anthropic libera nuevas versiones.
DEFAULT_MODEL = os.environ.get("CIBERSEGURIDAD_LLM_MODEL", "claude-sonnet-4-5-20250929")


class LLMNoDisponibleError(RuntimeError):
    """Se lanza cuando falta ANTHROPIC_API_KEY Y tampoco hay CLI de Claude disponible."""


@lru_cache(maxsize=None)
def _client(model: str) -> ChatAnthropic:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    return ChatAnthropic(model=model, api_key=api_key, timeout=120, max_retries=2)


def _call_via_api(system_prompt: str, user_message: str, model: str) -> str:
    llm = _client(model)
    response = llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
    )
    content = response.content
    if isinstance(content, list):
        # ChatAnthropic puede devolver bloques de contenido estructurado.
        text_parts = [
            block.get("text", "") for block in content if isinstance(block, dict)
        ]
        return "\n".join(part for part in text_parts if part)
    return str(content)


def _call_via_cli(system_prompt: str, user_message: str, model: str) -> str:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise LLMNoDisponibleError(
            "No hay ANTHROPIC_API_KEY configurada y tampoco se encontró el "
            "binario 'claude' en PATH. Copia .env.example a .env y completa "
            "la key, o instala Claude Code y abre sesión."
        )
    cmd = [
        claude_bin,
        "-p",
        user_message,
        "--system-prompt",
        system_prompt,
        "--output-format",
        "text",
        # Estas llamadas son de solo-análisis (priorizar/redactar/reportar
        # texto) — nunca deben tocar archivos ni correr comandos, sea cual
        # sea el prompt. Sin herramientas, no hay superficie que abusar.
        "--allowedTools",
        "",
    ]
    # Encontrado en una corrida real (agents/reporteria.py, ver
    # eval/live_run_report.md): 180s fijo a veces se queda corto — la
    # latencia de `claude -p` varía con la carga del sistema, no es
    # constante. Configurable en vez de subir un número a ciegas otra vez.
    cli_timeout = int(os.environ.get("VIGIA_CLAUDE_CLI_TIMEOUT", "300"))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=cli_timeout)
    except FileNotFoundError as exc:
        raise LLMNoDisponibleError(f"No se pudo ejecutar 'claude': {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise LLMNoDisponibleError(
            f"La CLI de Claude no respondió a tiempo ({cli_timeout}s)."
        ) from exc
    if result.returncode != 0:
        raise LLMNoDisponibleError(
            f"La CLI de Claude devolvió un error (código {result.returncode}): "
            f"{result.stderr.strip()[:500]}"
        )
    return result.stdout.strip()


def call_claude(system_prompt: str, user_message: str, model: str = DEFAULT_MODEL) -> str:
    """Llama a Claude con el system prompt EXACTO de la sección 7 y un mensaje de usuario.

    Devuelve solo el texto de la respuesta. Cada agente decide cómo
    interpretar/parsear su propia salida (JSON, texto libre, etc.) — este
    helper no impone estructura.

    Raises:
        LLMNoDisponibleError: si no hay ANTHROPIC_API_KEY configurada Y
            tampoco se encuentra la CLI de Claude instalada/autenticada.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _call_via_api(system_prompt, user_message, model)
    return _call_via_cli(system_prompt, user_message, model)
