"""Retención de screenshots de sitios de terceros -- `api/scheduler.py::run_screenshot_purge_once`.

`POST /antisuplantacion` guarda un PNG del sitio clon de cada dominio
lookalike (contenido de sitios web de TERCEROS). Hasta el cierre de este
hueco, ese directorio solo crecía, sin plazo ni criterio de borrado. Estos
tests confirman lo único que hace verdadera una política de retención
publicada: que lo viejo se borra y lo reciente se conserva.
"""

from __future__ import annotations

import os
import time

import api.scheduler as scheduler_module


def _crear_png(directorio, nombre: str, dias_de_antiguedad: float) -> str:
    ruta = os.path.join(str(directorio), nombre)
    with open(ruta, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
    envejecido = time.time() - dias_de_antiguedad * 86400
    os.utime(ruta, (envejecido, envejecido))
    return ruta


def test_purga_borra_lo_viejo_y_conserva_lo_reciente(tmp_path, monkeypatch):
    monkeypatch.setenv("VIGIA_SCREENSHOT_DIR", str(tmp_path))
    monkeypatch.setenv("VIGIA_SCREENSHOT_RETENCION_DIAS", "90")

    viejo = _crear_png(tmp_path, "clon-viejo.png", dias_de_antiguedad=120)
    justo_pasado = _crear_png(tmp_path, "clon-limite.png", dias_de_antiguedad=91)
    reciente = _crear_png(tmp_path, "clon-reciente.png", dias_de_antiguedad=3)

    borrados = scheduler_module.run_screenshot_purge_once()

    assert borrados == 2
    assert not os.path.exists(viejo)
    assert not os.path.exists(justo_pasado)
    assert os.path.exists(reciente)


def test_plazo_configurable_por_entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("VIGIA_SCREENSHOT_DIR", str(tmp_path))
    monkeypatch.setenv("VIGIA_SCREENSHOT_RETENCION_DIAS", "7")

    de_diez_dias = _crear_png(tmp_path, "diez-dias.png", dias_de_antiguedad=10)
    de_dos_dias = _crear_png(tmp_path, "dos-dias.png", dias_de_antiguedad=2)

    assert scheduler_module.run_screenshot_purge_once() == 1
    assert not os.path.exists(de_diez_dias)
    assert os.path.exists(de_dos_dias)


def test_directorio_inexistente_no_rompe(tmp_path, monkeypatch):
    """El job corre en el scheduler de fondo: nunca debe lanzar (un despliegue
    que aún no corrió ningún /antisuplantacion no tiene el directorio)."""
    monkeypatch.setenv("VIGIA_SCREENSHOT_DIR", str(tmp_path / "no-existe"))
    assert scheduler_module.run_screenshot_purge_once() == 0


def test_subdirectorios_se_ignoran(tmp_path, monkeypatch):
    monkeypatch.setenv("VIGIA_SCREENSHOT_DIR", str(tmp_path))
    monkeypatch.setenv("VIGIA_SCREENSHOT_RETENCION_DIAS", "1")
    sub = tmp_path / "subdir"
    sub.mkdir()
    envejecido = time.time() - 999 * 86400
    os.utime(str(sub), (envejecido, envejecido))

    assert scheduler_module.run_screenshot_purge_once() == 0
    assert sub.is_dir()


def test_el_job_queda_registrado_en_el_scheduler(monkeypatch):
    """La purga debe estar enganchada al scheduler existente, no ser una
    función suelta que nadie llama."""
    monkeypatch.setattr(scheduler_module, "_scheduler", None)
    scheduler = scheduler_module.start_scheduler()
    try:
        ids = {job.id for job in scheduler.get_jobs()}
        assert "vigia_purga_screenshots" in ids
    finally:
        scheduler_module.stop_scheduler()
