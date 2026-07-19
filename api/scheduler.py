"""Escaneo recurrente/programado — Fase 5 del plan maestro.

Primera pieza real de "vigilancia continua" (hoy todo es manual, botón
"Escanear ahora"). Corre `recon_pasivo` sobre cada activo de cada tenant a
intervalos regulares — a propósito SOLO recon pasivo, nunca Escaneo Activo
(Nuclei/ZAP), porque `autorizacion_firmada` hoy es un booleano que llega en
cada request, no un dato guardado por tenant/activo. Automatizar el Escaneo
Activo de forma segura requiere primero guardar esa autorización de forma
persistente (columna en `assets` o `tenants`, con el documento firmado
adjunto) — ver plan-proyecto-ciberseguridad.md, sección de brechas.

Uso: `start_scheduler()` desde `api/main.py::lifespan`, una sola vez por
proceso. `run_scan_cycle_once()` expone el ciclo completo para pruebas/CLI
sin esperar al intervalo.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from db.connection import get_conn
from orchestrator.graph import compile_graph
from orchestrator.state import new_state
from tools.asset_verification import asset_autorizado_para_escanear

logger = logging.getLogger("vigia.scheduler")

_scheduler: BackgroundScheduler | None = None
_compiled_graph = None


def _get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = compile_graph()
    return _compiled_graph


def run_scan_cycle_once() -> int:
    """Corre un ciclo de recon pasivo sobre todos los activos activos de todos los tenants.

    Devuelve cuántos activos se procesaron. Pensado para llamarse tanto
    desde el scheduler como directamente (pruebas, un cron externo, un
    comando manual) — la lógica del ciclo no depende de estar dentro de
    APScheduler.
    """
    conn = get_conn()
    try:
        activos = conn.execute(
            "SELECT id, tenant_id, tipo, valor, verificado FROM assets WHERE is_active = 1"
        ).fetchall()
    finally:
        conn.close()

    grafo = _get_graph()
    procesados = 0
    for activo in activos:
        # Mismo gate que POST /scan/POST /scan/activo/CertStream (ver
        # tools/asset_verification.py): un activo registrado pero no
        # verificado (y no exento por ser localhost/IP privada) no debe
        # recibir ni siquiera recon PASIVO automático recurrente -- antes de
        # este chequeo, registrar el dominio de un tercero real bastaba para
        # que este ciclo lo re-escaneara pasivamente cada N horas sin que el
        # tenant hubiera probado ningún vínculo real con ese dominio.
        if not asset_autorizado_para_escanear(activo["tipo"], activo["valor"], bool(activo["verificado"])):
            logger.info(
                "Activo %s omitido del ciclo recurrente -- no verificado ni exento",
                activo["valor"],
            )
            continue
        procesados += 1
        estado_inicial = new_state(
            target=activo["valor"],
            autorizacion_firmada=False,  # recurrente = siempre pasivo, ver docstring del módulo
            scope={"dominios": [activo["valor"]], "apps": [], "ips": [], "notas": ""},
            contexto_negocio="",
            antisuplantacion_habilitado=False,
        )
        try:
            estado_final = grafo.invoke(estado_inicial)
        except Exception:
            logger.exception("Ciclo de escaneo recurrente falló para activo %s", activo["valor"])
            continue

        scan_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT INTO scans
                    (id, tenant_id, asset_id, target, autorizacion_firmada, estado,
                     reporte_final, trace_log_json, completed_at)
                VALUES (?, ?, ?, ?, 0, 'completado', ?, ?, ?)
                """,
                (
                    scan_id,
                    activo["tenant_id"],
                    activo["id"],
                    activo["valor"],
                    estado_final.get("reporte_final"),
                    json.dumps(estado_final.get("trace_log") or []),
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    logger.info(
        "Ciclo de escaneo recurrente completado: %d/%d activo(s) procesados (resto omitido por verificación)",
        procesados,
        len(activos),
    )
    return procesados


def start_scheduler() -> BackgroundScheduler:
    """Arranca el scheduler en background (idempotente — llamar dos veces no duplica jobs).

    Intervalo configurable vía `VIGIA_SCAN_INTERVAL_HOURS` (default 6h).
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    interval_hours = float(os.environ.get("VIGIA_SCAN_INTERVAL_HOURS", "6"))
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        run_scan_cycle_once,
        "interval",
        hours=interval_hours,
        id="vigia_recon_recurrente",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler de escaneo recurrente iniciado (cada %.1fh)", interval_hours)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
