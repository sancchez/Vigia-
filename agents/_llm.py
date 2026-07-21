"""Helper compartido para los agentes que razonan con un LLM (sección 7 del plan).

No es un agente en sí — es utilidad común, igual que `tools/_shared.py` lo
es para los wrappers de herramientas. El agente de Verificación
(`agents/verificacion.py`) NO importa este módulo por diseño: su lógica es
determinista y no depende del LLM (sección 8.1, "separado del razonamiento
de la IA").

Tres backends, mismo contrato (`call_claude` — el nombre se conserva por
compatibilidad con los 6 call sites existentes, aunque ya no siempre llama
a Claude; ver `_resolve_provider()`):

1. **Anthropic, API directa** (`langchain_anthropic.ChatAnthropic`) si
   `ANTHROPIC_API_KEY` está configurada — el camino de producción real
   (facturación propia, sin depender de que la máquina tenga la CLI
   instalada y con sesión iniciada).
2. **Anthropic, CLI de Claude Code** (`claude -p`) si no hay API key pero el
   binario `claude` está disponible en PATH — reusa la sesión ya autenticada
   de quien esté corriendo el proyecto en su máquina, sin pagar ni configurar
   nada aparte. Pensado para desarrollo/demo, no para el servicio en
   producción con múltiples tenants (ahí sí hace falta la key real) NI para
   un host desplegado (Railway/Render no tienen la CLI instalada).
3. **OpenAI, API directa** (`langchain_openai.ChatOpenAI`) — alternativa
   genuinamente barata para producción cuando el presupuesto es la
   prioridad. Ver `_resolve_provider()` para cómo se elige, y
   `docs/despliegue.md` para el costo real estimado por reporte.

Selección de proveedor (`VIGIA_LLM_PROVIDER`, ver `_resolve_provider()`):
la variable es EXPLÍCITA pero OPCIONAL — si no se configura, se
auto-detecta según qué API key esté presente (Anthropic tiene prioridad
sobre OpenAI, para no romper el comportamiento por default de nadie que ya
tenía `ANTHROPIC_API_KEY` configurada antes de que esta opción existiera).
Esto evita el caso común de tener que tocar DOS variables (proveedor + key)
solo para usar la key que ya se tiene, pero deja la variable explícita
disponible para cuando alguien tenga ambas keys configuradas a la vez y
quiera forzar cuál se usa sin borrar ninguna.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

# Configurable por entorno para no atar el código a un solo model id
# a medida que Anthropic/OpenAI liberan nuevas versiones.
DEFAULT_MODEL = os.environ.get("CIBERSEGURIDAD_LLM_MODEL", "claude-sonnet-4-5-20250929")
# gpt-5.4-mini: $0.75/$4.50 por millón de tokens (input/output), precio real
# confirmado en developers.openai.com/api/docs/pricing en esta sesión — ver
# eval/live_run_report.md para el detalle completo de la comparación de
# precios que llevó a elegir este modelo (y no gpt-5.4-nano, más barato pero
# de calidad más arriesgada para razonamiento estructurado en español, ni
# Gemini/DeepSeek, más baratos aún pero no implementados esta sesión).
DEFAULT_OPENAI_MODEL = os.environ.get("VIGIA_OPENAI_MODEL", "gpt-5.4-mini")

_PROVEEDORES_VALIDOS = ("anthropic", "openai")


class LLMNoDisponibleError(RuntimeError):
    """Se lanza cuando el backend de LLM elegido (o el que se auto-detectó)
    no tiene forma de ejecutarse: falta la API key correspondiente, y para
    Anthropic tampoco hay CLI de Claude disponible como fallback (OpenAI no
    tiene un fallback de CLI — no hay equivalente gratuito de desarrollo)."""


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


@lru_cache(maxsize=None)
def _client_openai(model: str) -> ChatOpenAI:
    api_key = os.environ["OPENAI_API_KEY"]
    return ChatOpenAI(model=model, api_key=api_key, timeout=120, max_retries=2)


def _call_via_openai_api(system_prompt: str, user_message: str, model: str) -> str:
    """Mismo contrato que `_call_via_api`, contra la API de OpenAI.

    No tiene fallback de CLI — a diferencia de Claude, no existe una CLI de
    OpenAI gratuita reusando una sesión ya autenticada. Si no hay
    `OPENAI_API_KEY`, falla igual de explícito que el resto de este módulo.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise LLMNoDisponibleError(
            "VIGIA_LLM_PROVIDER='openai' (explícito o auto-detectado) pero no "
            "hay OPENAI_API_KEY configurada. Copia .env.example a .env y "
            "completa la key, o quita VIGIA_LLM_PROVIDER para volver al "
            "comportamiento por default (Anthropic)."
        )
    llm = _client_openai(model)
    response = llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
    )
    content = response.content
    if isinstance(content, list):
        # Mismo formato de bloques estructurados que puede devolver
        # ChatAnthropic — langchain_core normaliza ambos proveedores igual.
        text_parts = [
            block.get("text", "") for block in content if isinstance(block, dict)
        ]
        return "\n".join(part for part in text_parts if part)
    return str(content)


def _resolve_provider() -> str:
    """Decide qué backend de LLM usar para esta llamada.

    Ver el docstring del módulo para la justificación completa de la UX
    (explícito-pero-opcional, con auto-detección por key presente). Reglas:

    1. Si `VIGIA_LLM_PROVIDER` está configurada, se usa tal cual (validando
       que sea un valor soportado) — permite forzar un proveedor incluso si
       ambas keys están presentes.
    2. Si no está configurada: `ANTHROPIC_API_KEY` presente -> "anthropic";
       si no, `OPENAI_API_KEY` presente -> "openai"; si ninguna está
       presente -> "anthropic" (mismo default de siempre, que después cae
       al fallback de CLI en `call_claude()` — comportamiento IDÉNTICO al
       de antes de que esta función existiera cuando nada está configurado).
    """
    configurado = os.environ.get("VIGIA_LLM_PROVIDER", "").strip().lower()
    if configurado:
        if configurado not in _PROVEEDORES_VALIDOS:
            raise LLMNoDisponibleError(
                f"VIGIA_LLM_PROVIDER='{configurado}' no es válido. "
                f"Valores soportados: {', '.join(_PROVEEDORES_VALIDOS)}."
            )
        return configurado
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "anthropic"


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
        # user_message NO va aquí como argumento posicional — ver el
        # `input=user_message` en subprocess.run() más abajo. Encontrado real
        # probando `agents/revision_ia.py` con archivos reales de este mismo
        # repo (`api/main.py`, ~32KB): pasarlo como argv revienta con
        # `WinError 206: El nombre del archivo o la extensión es demasiado
        # largo` en Windows en cuanto el mensaje pasa de unos ~30-35KB
        # combinados con el resto del comando — es el límite real de
        # `CreateProcess` (~32.767 caracteres para la línea de comando
        # completa), no un timeout ni un límite de este proyecto. Los demás
        # agentes (priorizacion/remediacion/reporteria/cumplimiento) nunca lo
        # habían disparado porque envían resúmenes/JSON de hallazgos, no el
        # contenido crudo de archivos de código. `claude -p` SÍ lee el prompt
        # de stdin cuando no se le da como argumento posicional (confirmado
        # en esta sesión con un payload de 40.000 caracteres) — stdin no
        # tiene ese límite de longitud de línea de comando.
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
        # encoding="utf-8" explícito es OBLIGATORIO aquí, no cosmético: sin
        # esto, `text=True` decodifica con `locale.getpreferredencoding()`,
        # que en Windows suele ser cp1252, no utf-8. La CLI de Claude
        # siempre escribe stdout en UTF-8 — con cp1252 cada tilde/ñ del
        # español se corrompe en mojibake ("canciÃ³n" en vez de "canción").
        # Encontrado real en esta sesión: reventaba el texto de
        # agents/cumplimiento.py (y de reporteria.py/remediacion.py, que
        # comparten este mismo helper) cada vez que el fallback de CLI
        # generaba español con acentos. `input=user_message` (stdin) necesita
        # el mismo encoding="utf-8" explícito por la misma razón, en la
        # dirección contraria (Python codificando hacia el proceso hijo).
        result = subprocess.run(
            cmd,
            input=user_message,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=cli_timeout,
        )
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
    """Llama al LLM configurado con un system prompt y un mensaje de usuario.

    El nombre se conserva por compatibilidad con los call sites existentes
    (`priorizacion.py`, `remediacion.py`, `reporteria.py`, `cumplimiento.py`,
    `revision_ia.py`, `antisuplantacion.py`, `orquestador.py`) — todos
    invocan `call_claude(system_prompt, user_message)` sin tocar `model`, así
    que ninguno se ve afectado por el proveedor real que termine sirviendo
    la llamada. Ver `_resolve_provider()` para cómo se elige el backend.

    Devuelve solo el texto de la respuesta. Cada agente decide cómo
    interpretar/parsear su propia salida (JSON, texto libre, etc.) — este
    helper no impone estructura.

    Raises:
        LLMNoDisponibleError: si el proveedor resuelto no tiene forma de
            ejecutarse — para Anthropic, ni `ANTHROPIC_API_KEY` ni la CLI de
            Claude disponible; para OpenAI, falta `OPENAI_API_KEY` (no hay
            fallback de CLI); o si `VIGIA_LLM_PROVIDER` tiene un valor no
            soportado.
    """
    provider = _resolve_provider()
    if provider == "openai":
        # `model` por default es el id de Anthropic (DEFAULT_MODEL) — si el
        # llamador no pidió un modelo explícito, usar el default de OpenAI
        # en vez de pasarle un model id de Claude a ChatOpenAI.
        openai_model = model if model != DEFAULT_MODEL else DEFAULT_OPENAI_MODEL
        return _call_via_openai_api(system_prompt, user_message, openai_model)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _call_via_api(system_prompt, user_message, model)
    return _call_via_cli(system_prompt, user_message, model)
