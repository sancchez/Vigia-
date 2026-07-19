"""Regresión del bug real de esta sesión: `agents/_llm.py::_call_via_cli`
corrompía tildes/ñ en Windows.

Ver `docs/cumplimiento.md`, sección "Bug preexistente encontrado y
corregido: mojibake en el fallback de CLI": `subprocess.run(cmd,
capture_output=True, text=True, timeout=...)` sin `encoding` explícito
decodifica con `locale.getpreferredencoding(False)`, que en esta máquina
(y en Windows en general) suele ser `cp1252` -- pero la CLI de Claude
siempre escribe stdout en UTF-8. El fix fue una línea (`encoding="utf-8"`
explícito). Este archivo prueba que el fix sigue ahí y que de verdad
produce el texto correcto, no mojibake.

Tres niveles de prueba:

1. Guardia de regresión barata y portable: confirma que `_call_via_cli`
   sigue pasando `encoding="utf-8"` explícito a `subprocess.run` -- si
   alguien lo quita en un refactor futuro, este test falla en cualquier
   sistema operativo (Linux/CI incluido, donde el locale por defecto suele
   ser UTF-8 y el bug real no se manifestaría de otra forma).
2. Prueba de punta a punta con un subproceso REAL (no un mock que ya
   devuelve un string en memoria de Python -- eso no pasaría por ninguna
   decodificación real y no detectaría nada): un script de Python aparte
   escribe bytes UTF-8 genuinos con tildes/ñ a stdout, y `_call_via_cli`
   los recibe a través del mismo `subprocess.run(..., encoding="utf-8")`
   que usa el código real.
3. Prueba de control: confirma que decodificar esos mismos bytes como
   `cp1252` (el comportamiento por defecto en Windows sin el fix) SÍ
   corrompe el texto -- así queda demostrado que la prueba #2 de verdad
   habría fallado si el fix no existiera, y no que ambas codificaciones
   coinciden por casualidad con este texto de prueba.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agents._llm import _call_via_cli

TEXTO_ESPANOL_CON_TILDES = (
    "La contraseña de administración quedó expuesta en un backup público — "
    "la corrección es rotar credenciales y usar variables de entorno para "
    "la configuración. Año: 2026. Diseño de sesión inseguro también aplica."
)


def _escribir_script_que_emite_utf8(tmp_path: Path) -> Path:
    """Escribe un script Python standalone que emite bytes UTF-8 reales.

    Usa `sys.stdout.buffer.write(...)` (no `print()`) a propósito: así los
    bytes que salen son EXACTAMENTE los de `TEXTO_ESPANOL_CON_TILDES.encode
    ("utf-8")`, sin que la propia codificación de stdout de este script
    interfiera con lo que estamos probando.
    """
    script = tmp_path / "fake_claude_stdout.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.buffer.write({TEXTO_ESPANOL_CON_TILDES!r}.encode('utf-8'))\n",
        encoding="utf-8",
    )
    return script


def test_call_via_cli_pasa_encoding_utf8_explicito_a_subprocess_run(monkeypatch):
    """Guardia de regresión directa e independiente del sistema operativo."""
    capturado: dict = {}

    def _fake_run(cmd, **kwargs):
        capturado.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr("agents._llm.shutil.which", lambda _name: "claude")
    monkeypatch.setattr("agents._llm.subprocess.run", _fake_run)

    resultado = _call_via_cli("system", "user", "model")

    assert resultado == "ok"
    assert capturado.get("encoding") == "utf-8"
    assert capturado.get("text") is True


def test_call_via_cli_decodifica_tildes_y_ene_correctamente_de_punta_a_punta(tmp_path, monkeypatch):
    """Reproduce el bug real con un subproceso de verdad.

    Este es el test que "debería haber atrapado el bug real que se encontró
    y arregló esta sesión, si hubiera existido": si alguien vuelve a quitar
    `encoding="utf-8"` de `_call_via_cli`, en Windows este test empieza a
    fallar (mojibake), y en Linux/CI (locale UTF-8 por defecto) el test #1
    de arriba ya lo atrapa de todos modos.
    """
    script = _escribir_script_que_emite_utf8(tmp_path)
    real_subprocess_run = subprocess.run

    def _fake_run(cmd, input, capture_output, text, encoding, timeout):
        # cmd[0] es el 'claude_bin' inventado (irrelevante, se ignora) -- lo
        # mismo que `input` (el script fake ignora stdin, solo escribe a
        # stdout). Lo que importa es reusar el MISMO parámetro `encoding`
        # que decide el código real de _call_via_cli, contra un subproceso
        # real.
        real_cmd = [sys.executable, str(script)]
        return real_subprocess_run(
            real_cmd,
            input=input,
            capture_output=capture_output,
            text=text,
            encoding=encoding,
            timeout=timeout,
        )

    monkeypatch.setattr("agents._llm.shutil.which", lambda _name: "claude-fake-bin")
    monkeypatch.setattr("agents._llm.subprocess.run", _fake_run)

    resultado = _call_via_cli("system prompt", "user message", "model")

    assert resultado == TEXTO_ESPANOL_CON_TILDES
    assert "Ã" not in resultado  # firma típica de mojibake utf-8-decodificado-como-cp1252
    assert "ñ" in resultado
    assert "ó" in resultado
    assert "—" in resultado


def test_decodificar_como_cp1252_habria_corrompido_el_texto(tmp_path):
    """Prueba de control (no ejercita _call_via_cli): confirma que el bug
    real -- decodificar bytes UTF-8 como cp1252, que es lo que pasaba sin
    `encoding="utf-8"` explícito en Windows -- SÍ corrompe este texto de
    prueba. Sin este control, no sabríamos si el test de arriba de verdad
    tiene poder de detección o si el texto elegido pasaría con cualquier
    codificación por coincidencia.
    """
    script = _escribir_script_que_emite_utf8(tmp_path)

    resultado_roto = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="cp1252",
        timeout=30,
    ).stdout

    assert resultado_roto != TEXTO_ESPANOL_CON_TILDES
