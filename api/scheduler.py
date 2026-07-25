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

from api.notificaciones import enviar_notificacion
from db.connection import dict_from_row, get_conn
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


_ESTADOS_TERMINALES_SLA = ("cerrado", "verificado", "aceptado_riesgo")


def run_sla_escalation_once() -> int:
    """Barre findings abiertos con `sla_due_at` y dispara `sla_escalamiento`
    en dos umbrales -- Fase 5 (openspec/changes/fase5-notificaciones-alertas).

    Umbrales (ver spec.md, Requirement "Escalamiento de SLA"):
    - `75pct`: transcurrió >= 75% del plazo entre `created_at` y `sla_due_at`,
      y `sla_due_at` sigue en el futuro.
    - `vencido`: `sla_due_at` ya pasó.

    Cada umbral se notifica UNA sola vez por finding -- la idempotencia real
    la garantiza `enviar_notificacion` (clave compuesta `evento:finding_id`
    sobre `notifications`, ver api/notificaciones.py); este job simplemente
    calcula qué umbral corresponde y lo invoca, sin su propio estado.
    Findings `cerrado`/`verificado`/`aceptado_riesgo` quedan fuera de la
    consulta -- no se escala nada ya resuelto.

    Devuelve cuántos findings cruzaron algún umbral en esta corrida (no
    necesariamente cuántos emails se enviaron -- ver "sin regla activa"
    en enviar_notificacion, que degrada en silencio).
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT id, tenant_id, tipo, endpoint, created_at, sla_due_at
            FROM findings
            WHERE sla_due_at IS NOT NULL
              AND estado NOT IN ({", ".join("?" for _ in _ESTADOS_TERMINALES_SLA)})
            """,
            _ESTADOS_TERMINALES_SLA,
        ).fetchall()

        ahora = datetime.now(timezone.utc)
        escalados = 0
        for row in rows:
            finding = dict_from_row(row)
            try:
                creado = datetime.fromisoformat(finding["created_at"])
                vence = datetime.fromisoformat(finding["sla_due_at"])
            except (TypeError, ValueError):
                continue
            if creado.tzinfo is None:
                creado = creado.replace(tzinfo=timezone.utc)
            if vence.tzinfo is None:
                vence = vence.replace(tzinfo=timezone.utc)

            if ahora >= vence:
                umbral = "vencido"
            else:
                plazo_total = (vence - creado).total_seconds()
                transcurrido = (ahora - creado).total_seconds()
                if plazo_total > 0 and (transcurrido / plazo_total) >= 0.75:
                    umbral = "75pct"
                else:
                    umbral = None

            if umbral is None:
                continue

            escalados += 1
            enviar_notificacion(
                conn,
                finding["tenant_id"],
                f"sla_escalamiento:{umbral}",
                finding["id"],
                asunto=(
                    f"[Vigia] SLA vencido: {finding['tipo']}"
                    if umbral == "vencido"
                    else f"[Vigia] SLA al 75% del plazo: {finding['tipo']}"
                ),
                cuerpo=(
                    f"Un hallazgo {'venció' if umbral == 'vencido' else 'está por vencer (75% del plazo)'} "
                    f"su SLA de remediación.\n\n"
                    f"Tipo: {finding['tipo']}\n"
                    f"Endpoint: {finding['endpoint']}\n"
                    f"Finding ID: {finding['id']}\n"
                    f"Vence: {finding['sla_due_at']}\n"
                ),
            )
    finally:
        conn.close()

    logger.info("Escalamiento de SLA completado: %d finding(s) cruzaron algún umbral", escalados)
    return escalados


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

    # Fase 5 (openspec/changes/fase5-notificaciones-alertas): mismo patrón de
    # job periódico que el de arriba, intervalo propio y más corto por
    # default (6h) -- el escalamiento de SLA es sensible al tiempo de forma
    # distinta al recon pasivo.
    sla_interval_hours = float(os.environ.get("VIGIA_SLA_CHECK_INTERVAL_HOURS", "6"))
    _scheduler.add_job(
        run_sla_escalation_once,
        "interval",
        hours=sla_interval_hours,
        id="vigia_sla_escalamiento",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "Scheduler iniciado -- recon recurrente cada %.1fh, escalamiento de SLA cada %.1fh",
        interval_hours,
        sla_interval_hours,
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
