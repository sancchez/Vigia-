"""`tools/scan.py::_run_zap_script` -- limpieza real de contenedores Docker en timeout.

Ver eval/live_run_report.md Corrida 12 (bug real encontrado en vivo): antes
de este fix, cuando `subprocess.run(..., timeout=...)` expiraba, el proceso
cliente `docker run` moría pero el contenedor seguía corriendo del lado del
daemon (`--rm` solo limpia cuando el contenedor termina por sí mismo, no
cuando el cliente que lo lanzó se desconecta) -- quedaba huérfano
indefinidamente. Peor: `tempfile.TemporaryDirectory()` borraba el
bind-mount del host en cuanto la excepción de timeout se propagaba, así que
un reporte que el contenedor huérfano alcanzara a escribir después era
irrecuperable por diseño.

El fix real:
1. Contenedor con `--name vigia-zap-<scan_id o uuid>` explícito.
2. En timeout: ventana de gracia breve (carrera real: puede estar
   terminando justo en ese momento) y solo entonces se decide.
3. Si de verdad sigue corriendo: `docker stop`/`docker rm` (no huérfano) y
   se relanza el timeout real -- nada que recuperar.
4. Si ya terminó (dentro o fuera de la gracia): el workdir NO se ha borrado
   todavía (a diferencia del bug original) -- se intenta parsear el reporte
   antes de limpiar cualquier cosa.

Estos tests son deterministas y rápidos (mockean `run_command`, y las
funciones `docker_container_running`/`docker_force_remove_container` de
`tools/_shared.py`, y colapsan la ventana de gracia a 0) -- no requieren
Docker real ni un ZAP real corriendo, para que puedan correr en cualquier
CI sin depender de una corrida lenta real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.scan as scan_module
from tools._shared import ToolResult, ToolTimeoutError


def _workdir_de_cmd(cmd: list[str]) -> Path:
    """Extrae el path del host montado vía `-v <workdir>:/zap/wrk/:rw` del comando docker."""
    idx = cmd.index("-v")
    mount_arg = cmd[idx + 1]
    host_path = mount_arg.split(":/zap/wrk/")[0]
    return Path(host_path)


def _nombre_contenedor_de_cmd(cmd: list[str]) -> str:
    idx = cmd.index("--name")
    return cmd[idx + 1]


@pytest.fixture(autouse=True)
def _sin_docker_real(monkeypatch):
    """Estos tests nunca deben invocar Docker de verdad -- todo pasa por mocks."""
    monkeypatch.setattr(scan_module, "require_binary", lambda *_a, **_k: "docker")
    # Colapsa la ventana de gracia real (3s) para que los tests no tarden.
    monkeypatch.setattr(scan_module, "_TIMEOUT_GRACE_SECONDS", 0)
    monkeypatch.setattr(scan_module.time, "sleep", lambda _s: None)


def test_container_se_nombra_con_scan_id_explicito(monkeypatch):
    """El contenedor debe tener un --name predecible (no solo --rm) para
    poder referenciarlo después de un timeout -- ver docstring del módulo."""
    capturado = {}

    def _fake_run_command(tool, cmd, timeout=300, cwd=None):
        capturado["cmd"] = cmd
        return ToolResult(tool=tool, command=list(cmd), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(scan_module, "run_command", _fake_run_command)

    scan_module.run_zap_baseline("http://example.test", scan_id="abc123")

    assert _nombre_contenedor_de_cmd(capturado["cmd"]) == "vigia-zap-abc123"
    assert "--rm" in capturado["cmd"]


def test_container_se_nombra_con_uuid_si_no_hay_scan_id(monkeypatch):
    capturado = {}

    def _fake_run_command(tool, cmd, timeout=300, cwd=None):
        capturado["cmd"] = cmd
        return ToolResult(tool=tool, command=list(cmd), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(scan_module, "run_command", _fake_run_command)

    scan_module.run_zap_baseline("http://example.test")

    nombre = _nombre_contenedor_de_cmd(capturado["cmd"])
    assert nombre.startswith("vigia-zap-")
    assert nombre != "vigia-zap-"


def test_timeout_con_contenedor_realmente_corriendo_se_limpia_y_relanza(monkeypatch):
    """El contenedor sigue vivo tras la ventana de gracia -- debe forzarse
    su limpieza (docker stop/rm, no dejarlo huérfano) y propagar el
    ToolTimeoutError real (no hay nada que recuperar)."""
    llamadas_cleanup = []

    def _fake_run_command(tool, cmd, timeout=300, cwd=None):
        raise ToolTimeoutError(tool, timeout, cmd)

    def _fake_running(binary, name):
        return True  # sigue corriendo de verdad, tanto antes como después de la gracia

    def _fake_force_remove(binary, name):
        llamadas_cleanup.append(name)

    monkeypatch.setattr(scan_module, "run_command", _fake_run_command)
    monkeypatch.setattr(scan_module, "docker_container_running", _fake_running)
    monkeypatch.setattr(scan_module, "docker_force_remove_container", _fake_force_remove)

    with pytest.raises(ToolTimeoutError):
        scan_module.run_zap_baseline("http://example.test", scan_id="huerfano1")

    assert llamadas_cleanup == ["vigia-zap-huerfano1"]


def test_timeout_pero_contenedor_ya_termino_sin_reporte_sigue_siendo_timeout(monkeypatch):
    """Terminó (o ya no existe) dentro de la ventana de gracia, pero no
    escribió ningún reporte usable -- debe seguir siendo un timeout real,
    no un éxito silencioso con hallazgos vacíos."""

    def _fake_run_command(tool, cmd, timeout=300, cwd=None):
        raise ToolTimeoutError(tool, timeout, cmd)

    monkeypatch.setattr(scan_module, "run_command", _fake_run_command)
    monkeypatch.setattr(scan_module, "docker_container_running", lambda *_a: False)
    monkeypatch.setattr(scan_module, "docker_force_remove_container", lambda *_a: None)

    with pytest.raises(ToolTimeoutError):
        scan_module.run_zap_baseline("http://example.test", scan_id="sinreporte1")


def test_timeout_con_reporte_recuperado_por_la_carrera_real(monkeypatch):
    """La carrera real documentada en Corrida 12: el contenedor termina (y
    escribe su reporte) justo cuando Python ya había marcado timeout. El
    workdir NO debe borrarse antes de intentar parsear ese reporte -- si el
    fix funciona, los hallazgos se recuperan en vez de perderse."""

    def _fake_run_command(tool, cmd, timeout=300, cwd=None):
        workdir = _workdir_de_cmd(cmd)
        assert workdir.exists(), "el workdir debe seguir existiendo al momento del timeout"
        reporte = {
            "site": [
                {
                    "alerts": [
                        {"name": "SQL Injection", "riskdesc": "High (Medium)", "instances": [{"uri": "http://example.test/login"}]}
                    ]
                }
            ]
        }
        (workdir / "zap-report.json").write_text(json.dumps(reporte), encoding="utf-8")
        raise ToolTimeoutError(tool, timeout, cmd)

    monkeypatch.setattr(scan_module, "run_command", _fake_run_command)
    monkeypatch.setattr(scan_module, "docker_container_running", lambda *_a: False)
    monkeypatch.setattr(scan_module, "docker_force_remove_container", lambda *_a: None)

    resultado = scan_module.run_zap_baseline("http://example.test", scan_id="recuperado1")

    assert len(resultado.findings) == 1
    assert resultado.findings[0]["name"] == "SQL Injection"
    assert "recuperado" in resultado.raw.stderr.lower() or "termin" in resultado.raw.stderr.lower()


def test_workdir_se_borra_despues_de_recuperar_el_reporte(monkeypatch, tmp_path):
    """Confirma que el bind-mount SÍ se limpia eventualmente (no queda
    basura en disco) -- solo que después de haber podido leerlo, no antes."""
    workdirs_usados = []

    def _fake_run_command(tool, cmd, timeout=300, cwd=None):
        workdir = _workdir_de_cmd(cmd)
        workdirs_usados.append(workdir)
        (workdir / "zap-report.json").write_text(json.dumps({"site": []}), encoding="utf-8")
        raise ToolTimeoutError(tool, timeout, cmd)

    monkeypatch.setattr(scan_module, "run_command", _fake_run_command)
    monkeypatch.setattr(scan_module, "docker_container_running", lambda *_a: False)
    monkeypatch.setattr(scan_module, "docker_force_remove_container", lambda *_a: None)

    scan_module.run_zap_baseline("http://example.test", scan_id="limpieza1")

    assert not workdirs_usados[0].exists()


def test_no_recuperado_docker_stop_y_rm_se_llaman_ambos(monkeypatch):
    """`docker_force_remove_container` es responsable de stop+rm -- este
    test confirma que _run_zap_script SIEMPRE lo invoca en timeout (tanto
    si terminó como si no), en vez de dejar la limpieza a medias."""
    llamado = {"veces": 0}

    def _fake_run_command(tool, cmd, timeout=300, cwd=None):
        raise ToolTimeoutError(tool, timeout, cmd)

    def _fake_force_remove(binary, name):
        llamado["veces"] += 1

    monkeypatch.setattr(scan_module, "run_command", _fake_run_command)
    monkeypatch.setattr(scan_module, "docker_container_running", lambda *_a: False)
    monkeypatch.setattr(scan_module, "docker_force_remove_container", _fake_force_remove)

    with pytest.raises(ToolTimeoutError):
        scan_module.run_zap_baseline("http://example.test", scan_id="conteo1")

    assert llamado["veces"] == 1
