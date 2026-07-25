"""Escaneo recurrente/programado — Fase 5 del plan maestro.

Primera pieza real de "vigilancia continua" (hoy todo es manual, botón
"Escanear ahora"). Corre `recon_pasivo` sobre cada activo de cada tenant a
intervalos regulares.

**Fase 6a** (openspec/changes/fase6-endurecimiento-produccion) cierra el
bloqueo que este módulo documentaba: `assets.autorizacion_activa` ya existe
de forma persistente desde Fase 0 (columna + documento firmado adjunto), así
que este ciclo ahora SÍ puede disparar Escaneo Activo real
(`tools.zap_api.run_checkpointed_active_scan`) para los activos que la
tienen marcada -- nunca a partir de un booleano de request, siempre de la
columna persistida (ver `Decisión 6a.3` en design.md). Cada activo también
puede pausarse (`scan_paused`) u opt-out del intervalo global con su propia
frecuencia (`scan_frequency_hours`) -- ver `Decisión 6a.4`.

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

from agents._llm import set_llm_context
from api.notificaciones import enviar_notificacion
from db.connection import dict_from_row, get_conn
from orchestrator.graph import compile_graph
from orchestrator.state import new_state
from tools._shared import ToolExecutionError
from tools.asset_verification import asset_autorizado_para_escanear
from tools.zap_api import run_checkpointed_active_scan

logger = logging.getLogger("vigia.scheduler")

_scheduler: BackgroundScheduler | None = None
_compiled_graph = None


def _get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = compile_graph()
    return _compiled_graph


def _cumple_frecuencia_programada(conn, asset_id: str, frequency_hours: int | None) -> bool:
    """¿Ya pasó suficiente tiempo desde el último scan de este activo?

    `frequency_hours is None` -> el activo no tiene frecuencia propia, usa
    siempre el intervalo global de `VIGIA_SCAN_INTERVAL_HOURS` (comportamiento
    IDÉNTICO al de antes de Fase 6a: due en cada tick). Si el activo nunca
    tuvo un scan, también está due -- no hay "último scan" del que medir.
    """
    if frequency_hours is None:
        return True
    row = conn.execute(
        "SELECT MAX(created_at) AS ultimo FROM scans WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    ultimo = row["ultimo"] if row else None
    if not ultimo:
        return True
    try:
        # `scans.created_at` es siempre el default `CURRENT_TIMESTAMP` de
        # ambos motores (nunca se pasa explícito en el INSERT) -- formato
        # estable "YYYY-MM-DD HH:MM:SS" (ver db/connection.py, truncado a
        # segundos también en Postgres vía _TimestampStrLoader).
        ultimo_dt = datetime.strptime(str(ultimo)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        # Timestamp con forma inesperada -- degradar a "due" en vez de
        # excluir el activo del ciclo para siempre por un parseo que falla.
        return True
    horas_transcurridas = (datetime.now(timezone.utc) - ultimo_dt).total_seconds() / 3600
    return horas_transcurridas >= frequency_hours


def _ejecutar_ciclo_pasivo(grafo, activo, scan_id: str) -> None:
    set_llm_context(activo["tenant_id"], scan_id)
    estado_inicial = new_state(
        target=activo["valor"],
        autorizacion_firmada=False,  # pasivo: nunca ejecuta Escaneo Activo
        scope={"dominios": [activo["valor"]], "apps": [], "ips": [], "notas": ""},
        contexto_negocio="",
        antisuplantacion_habilitado=False,
    )
    try:
        estado_final = grafo.invoke(estado_inicial)
    except Exception:
        logger.exception("Ciclo de escaneo recurrente (pasivo) falló para activo %s", activo["valor"])
        return

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


def _ejecutar_ciclo_activo(activo, scan_id: str) -> None:
    """Escaneo activo real (ZAP) para un activo con `autorizacion_activa=1` verificado.

    El gate real ya se aplicó en `run_scan_cycle_once` (columna persistida +
    `asset_autorizado_para_escanear`, defensa en capas) -- esta función nunca
    decide autorización, solo ejecuta y persiste. Cualquier error del
    escaneo (ej. ZAP/Docker no disponible) se loguea y el ciclo continúa con
    el resto de activos -- nunca se marca `autorizacion_firmada` a partir de
    algo que no corrió de verdad.
    """
    set_llm_context(activo["tenant_id"], scan_id)
    target_url = activo["valor"]
    if "://" not in target_url:
        target_url = f"http://{target_url}"
    minutes = int(os.environ.get("VIGIA_SCAN_ACTIVE_SCHEDULED_MINUTES", "20"))

    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO scans (id, tenant_id, asset_id, target, autorizacion_firmada, estado, trace_log_json)
            VALUES (?, ?, ?, ?, 1, 'corriendo', '[]')
            """,
            (scan_id, activo["tenant_id"], activo["id"], target_url),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        resultado = run_checkpointed_active_scan(
            target_url, minutes=minutes, bearer_token=None, ajax_spider=True, scan_id=scan_id
        )
        estado_final = "completado"
        reporte = f"Escaneo activo programado completado -- {resultado.detalle_final}"
    except ToolExecutionError as exc:
        estado_final = "error"
        reporte = f"Escaneo activo programado falló: {exc}"
    except Exception as exc:  # noqa: BLE001 -- último punto de captura de este ciclo
        logger.exception("Ciclo de escaneo recurrente (activo) falló para activo %s", target_url)
        estado_final = "error"
        reporte = f"Escaneo activo programado falló con un error inesperado: {exc}"

    completed_at = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE scans SET estado = ?, reporte_final = ?, completed_at = ? WHERE id = ?",
            (estado_final, reporte, completed_at, scan_id),
        )
        conn.commit()
    finally:
        conn.close()


def run_scan_cycle_once() -> int:
    """Corre un ciclo de escaneo sobre todos los activos activos de todos los tenants.

    Por activo, decide pasivo vs. activo según `autorizacion_activa`
    (columna persistida, ver docstring del módulo) -- nunca a partir de un
    booleano de request. Devuelve cuántos activos se procesaron. Pensado
    para llamarse tanto desde el scheduler como directamente (pruebas, un
    cron externo, un comando manual) — la lógica del ciclo no depende de
    estar dentro de APScheduler.
    """
    conn = get_conn()
    try:
        activos = conn.execute(
            "SELECT id, tenant_id, tipo, valor, verificado, autorizacion_activa, "
            "scan_frequency_hours, scan_paused FROM assets WHERE is_active = 1"
        ).fetchall()
    finally:
        conn.close()

    grafo = _get_graph()
    procesados = 0
    for activo in activos:
        if activo["scan_paused"]:
            logger.info("Activo %s omitido del ciclo recurrente -- pausado", activo["valor"])
            continue
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

        conn_frecuencia = get_conn()
        try:
            due = _cumple_frecuencia_programada(conn_frecuencia, activo["id"], activo["scan_frequency_hours"])
        finally:
            conn_frecuencia.close()
        if not due:
            logger.info(
                "Activo %s omitido del ciclo recurrente -- aún no cumple su frecuencia programada (%sh)",
                activo["valor"],
                activo["scan_frequency_hours"],
            )
            continue

        procesados += 1
        scan_id = str(uuid.uuid4())
        if activo["autorizacion_activa"]:
            _ejecutar_ciclo_activo(activo, scan_id)
        else:
            _ejecutar_ciclo_pasivo(grafo, activo, scan_id)

    logger.info(
        "Ciclo de escaneo recurrente completado: %d/%d activo(s) procesados (resto omitido por verificación/pausa/frecuencia)",
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
