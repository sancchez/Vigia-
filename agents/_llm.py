"""Helper compartido para los agentes que razonan con Claude (sección 7 del plan).

No es un agente en sí — es utilidad común, igual que `tools/_shared.py` lo
es para los wrappers de herramientas. El agente de Verificación
(`agents/verificacion.py`) NO importa este módulo por diseño: su lógica es
determinista y no depende del LLM (sección 8.1, "separado del razonamiento
de la IA").
"""

from __future__ import annotations

import os
from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

# Configurable por entorno para no atar el código a un solo model id
# a medida que Anthropic libera nuevas versiones.
DEFAULT_MODEL = os.environ.get("CIBERSEGURIDAD_LLM_MODEL", "claude-sonnet-4-5-20250929")


class LLMNoDisponibleError(RuntimeError):
    """Se lanza cuando falta ANTHROPIC_API_KEY — nunca al importar el módulo."""


@lru_cache(maxsize=None)
def _client(model: str) -> ChatAnthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMNoDisponibleError(
            "ANTHROPIC_API_KEY no está configurada. Copia .env.example a .env "
            "en la raíz del proyecto y completa la key, o expórtala en el entorno."
        )
    return ChatAnthropic(model=model, api_key=api_key, timeout=120, max_retries=2)


def call_claude(system_prompt: str, user_message: str, model: str = DEFAULT_MODEL) -> str:
    """Llama a Claude con el system prompt EXACTO de la sección 7 y un mensaje de usuario.

    Devuelve solo el texto de la respuesta. Cada agente decide cómo
    interpretar/parsear su propia salida (JSON, texto libre, etc.) — este
    helper no impone estructura.

    Raises:
        LLMNoDisponibleError: si no hay ANTHROPIC_API_KEY configurada.
    """
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
