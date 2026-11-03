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
import time
import uuid
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from agents._llm import set_llm_context
from agents.memoria import contexto_historico
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
_compiled_graph_db_url = None


def _get_graph():
    # Mismo criterio que `api/main.py::_get_graph`: el grafo compilado lleva un
    # checkpointer atado a una `DATABASE_URL` concreta, así que la caché se
    # invalida si esa URL cambia.
    global _compiled_graph, _compiled_graph_db_url
    url_actual = os.environ.get("DATABASE_URL", "sqlite:///./ciberseguridad.db")
    if _compiled_graph is None or _compiled_graph_db_url != url_actual:
        _compiled_graph = compile_graph()
        _compiled_graph_db_url = url_actual
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
        # Fase 3: memoria entre corridas. Un scan recurrente es justo donde más
        # importa — es el que vuelve a ver el mismo hallazgo semana tras semana.
        contexto_historico=contexto_historico(activo["tenant_id"], activo["valor"]),
    )
    try:
        # `thread_id = scan_id` (ver orchestrator/graph.py::_default_saver):
        # el scan_id ya viene generado por el caller de este ciclo.
        estado_final = grafo.invoke(
            estado_inicial, config={"configurable": {"thread_id": scan_id}}
        )
    except Exception:
        logger.exception("Ciclo de escaneo recurrente (pasivo) falló para activo %s", activo["valor"])
        return

    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        conn.execute(
            """
            -- `user_id` explícitamente NULL (fix H-4): este scan lo dispara el
            -- scheduler, no una persona. Dejarlo NULL es el dato honesto -- la
            -- trazabilidad de "quién autorizó" para el camino recurrente vive
            -- en `assets.autorizado_por`/`autorizacion_doc_url`, escritas por
            -- `POST /assets/{id}/autorizacion-activa`.
            INSERT INTO scans
                (id, tenant_id, asset_id, user_id, target, autorizacion_firmada, estado,
                 reporte_final, trace_log_json, completed_at)
            VALUES (?, ?, ?, NULL, ?, 0, 'completado', ?, ?, ?)
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
            -- `user_id` NULL: disparado por el scheduler. Ver el comentario en
            -- `_ejecutar_ciclo_pasivo`.
            INSERT INTO scans (id, tenant_id, asset_id, user_id, target, autorizacion_firmada, estado, trace_log_json)
            VALUES (?, ?, ?, NULL, ?, 1, 'corriendo', '[]')
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


def _objetivos_de_marca() -> list[dict]:
    """Targets de vigilancia de marca: dominios verificados + marcas ancladas a uno.

    Dos fuentes, mismo gate legal que ya aplica el resto del sistema:

    1. `assets` tipo `dominio`, activos, que pasan
       `asset_autorizado_para_escanear` (verificados o exentos). Un dominio
       registrado pero no verificado NO se vigila -- si no, bastaría con
       escribir el dominio de un tercero para que Vigia hiciera
       reconocimiento recurrente sobre su marca en nombre de quien lo cargó.
    2. `watched_brands` activas CON `dominio_asociado` no vacío, es decir
       ancladas a un dominio-activo ya verificado del tenant (el gate que
       aplica `api/main.py::_dominio_verificado_para_marca` al registrarlas).
       Mismo criterio exacto que `api/certstream_listener.py::_build_variant_map`,
       a propósito: las dos vías de vigilancia continua tienen que vigilar el
       mismo universo, o el cliente vería hallazgos distintos según qué
       infraestructura tenga levantada.

    Las marcas heredan `asset_id = None` (no hay un activo detrás; la columna
    es nullable en `scans`).
    """
    conn = get_conn()
    try:
        activos = conn.execute(
            "SELECT id, tenant_id, tipo, valor, verificado, scan_paused "
            "FROM assets WHERE tipo = 'dominio' AND is_active = 1"
        ).fetchall()
        marcas = conn.execute(
            """
            SELECT tenant_id, marca FROM watched_brands
            WHERE is_active = 1
              AND dominio_asociado IS NOT NULL
              AND TRIM(dominio_asociado) <> ''
            """
        ).fetchall()
    finally:
        conn.close()

    objetivos: list[dict] = []
    vistos: set[tuple[str, str]] = set()
    for fila in activos:
        activo = dict_from_row(fila)
        if activo["scan_paused"]:
            continue
        if not asset_autorizado_para_escanear("dominio", activo["valor"], bool(activo["verificado"])):
            continue
        clave = (activo["tenant_id"], (activo["valor"] or "").strip().lower())
        if clave in vistos:
            continue
        vistos.add(clave)
        objetivos.append(
            {"tenant_id": activo["tenant_id"], "asset_id": activo["id"], "target": activo["valor"]}
        )

    for fila in marcas:
        marca = dict_from_row(fila)
        # Misma normalización marca->dominio que usa el listener de CertStream
        # (`_normalizar_marca_a_dominio`), importada de allí para que no haya
        # dos definiciones del mismo criterio que puedan divergir.
        from api.certstream_listener import _normalizar_marca_a_dominio

        target = _normalizar_marca_a_dominio(marca["marca"])
        clave = (marca["tenant_id"], target)
        if clave in vistos:
            continue
        vistos.add(clave)
        objetivos.append({"tenant_id": marca["tenant_id"], "asset_id": None, "target": target})

    return objetivos


def _marca_due(conn, tenant_id: str, target: str, horas: float) -> bool:
    """¿Pasaron `horas` desde el último barrido de marca de este target?

    Se mide sobre las filas `scans` que este mismo ciclo escribe
    (`reporte_final` con el prefijo conocido), no sobre `findings`: si el
    barrido anterior no encontró nada, igual corrió, y volver a correrlo cada
    tick sería sondear infraestructura de terceros más seguido de lo que el
    cliente pidió.
    """
    row = conn.execute(
        """
        SELECT MAX(created_at) AS ultimo FROM scans
        WHERE tenant_id = ? AND target = ? AND reporte_final LIKE ?
        """,
        (tenant_id, target, f"{_PREFIJO_REPORTE_BARRIDO}%"),
    ).fetchone()
    ultimo = row["ultimo"] if row else None
    if not ultimo:
        return True
    try:
        ultimo_dt = datetime.strptime(str(ultimo)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - ultimo_dt).total_seconds() / 3600 >= horas


_PREFIJO_REPORTE_BARRIDO = "Barrido programado de marca"


def run_brand_cycle_once() -> int:
    """Barrido PROGRAMADO de suplantación de marca sobre todos los tenants.

    Por qué existe (el agujero que cierra)
    --------------------------------------
    Hasta esta función, "vigilancia continua de marca" era una promesa que el
    sistema NO cumplía por ninguna vía:

    * `run_scan_cycle_once` (el ciclo recurrente que sí existía) construye su
      estado con `antisuplantacion_habilitado=False` -- literal, ver
      `_ejecutar_ciclo_pasivo`. El barrido recurrente NUNCA miraba dominios
      clon.
    * `api/certstream_listener.py` sí vigila en vivo, pero apuntaba por
      default a `wss://certstream.calidog.io`, un feed descontinuado que
      acepta el handshake y no transmite nada. Sin levantar el feed
      self-hosted (`deploy/certstream/docker-compose.yml`), el listener
      arrancaba "sano" y se quedaba mudo para siempre.

    Resultado: la única forma real de detectar un dominio clon era que una
    persona apretara el botón "Proteger marca" (`POST /antisuplantacion`).
    Eso es una herramienta bajo demanda, no vigilancia -- y el producto la
    vendía como vigilancia.

    Esta función es la vía que funciona SIN infraestructura extra: corre el
    mismo `agents/antisuplantacion.py::node()` que el botón, sobre cada
    objetivo de `_objetivos_de_marca()`, cada `VIGIA_BRAND_CYCLE_HOURS`
    (default 24), y persiste los hallazgos por el mismo camino que
    `POST /antisuplantacion` (`_extraer_findings_antisuplantacion` +
    `_registrar_findings_con_ciclo_de_vida`), de modo que un clon detectado
    de fondo entra al ciclo de vida, hereda SLA, dispara notificación y
    aparece en el panel exactamente igual que uno detectado a mano.

    Latencia honesta: horas (el intervalo), no minutos. CertStream sigue
    siendo la vía de baja latencia cuando su feed está levantado; las dos
    conviven porque la deduplicación de `_registrar_findings_con_ciclo_de_vida`
    ya evita que el mismo dominio se cuente dos veces.

    Cortesía de red: no se toca `VIGIA_SONDEO_DELAY_*` -- el sondeo real lo
    hace `tools/antisuplantacion.py` con los defaults conservadores ya
    configurados. Este ciclo solo decide CUÁNDO correr, no qué tan agresivo
    es el sondeo.

    Nunca lanza: un objetivo que falla se registra y el ciclo sigue con el
    resto (mismo criterio que `run_scan_cycle_once`). Devuelve cuántos
    objetivos se barrieron de verdad.
    """
    if os.environ.get("VIGIA_BRAND_CYCLE_ENABLED", "true").strip().lower() in ("0", "false", "no"):
        logger.info("Barrido programado de marca deshabilitado por VIGIA_BRAND_CYCLE_ENABLED")
        return 0

    try:
        horas = float(os.environ.get("VIGIA_BRAND_CYCLE_HOURS", "24"))
    except ValueError:
        horas = 24.0

    # Import diferido: `api/main.py` importa este módulo en su lifespan, así
    # que un import a nivel de módulo sería circular. Mismo patrón de
    # reutilización deliberada -- estas tres funciones son el camino de
    # persistencia YA probado de `POST /antisuplantacion`; reimplementarlas
    # acá crearía una segunda verdad sobre cómo se guarda un hallazgo de marca.
    from api.main import (
        VIGIA_SCREENSHOT_DIR,
        _enriquecer_dominios_clon,
        _extraer_findings_antisuplantacion,
        _registrar_findings_con_ciclo_de_vida,
    )
    from agents.antisuplantacion import node as antisuplantacion_node

    objetivos = _objetivos_de_marca()
    barridos = 0
    for objetivo in objetivos:
        tenant_id = objetivo["tenant_id"]
        target = objetivo["target"]

        conn = get_conn()
        try:
            due = _marca_due(conn, tenant_id, target, horas)
        finally:
            conn.close()
        if not due:
            logger.info("Marca %s omitida del barrido -- aún no cumple su frecuencia (%.1fh)", target, horas)
            continue

        scan_id = str(uuid.uuid4())
        set_llm_context(tenant_id, scan_id)
        try:
            resultado = antisuplantacion_node(
                new_state(target=target, antisuplantacion_habilitado=True)
            )
            señales = resultado.get("antisuplantacion_findings") or []
            _enriquecer_dominios_clon(señales, VIGIA_SCREENSHOT_DIR, target)
            filas = _extraer_findings_antisuplantacion(target, señales)
        except Exception:  # noqa: BLE001 -- un objetivo roto no puede tumbar el ciclo
            logger.exception("Barrido programado de marca falló para %s", target)
            continue

        now = datetime.now(timezone.utc).isoformat()
        conn = get_conn()
        try:
            conn.execute(
                """
                -- `autorizacion_firmada = 0`: este barrido NO ejecuta escaneo
                -- activo contra nadie, solo consulta DNS/RDAP y sondea de forma
                -- pasiva. `user_id` NULL por la misma razón que el resto de los
                -- jobs del scheduler: lo dispara el sistema, no una persona.
                INSERT INTO scans
                    (id, tenant_id, asset_id, user_id, target, autorizacion_firmada, estado,
                     reporte_final, trace_log_json, completed_at)
                VALUES (?, ?, ?, NULL, ?, 0, 'completado', ?, ?, ?)
                """,
                (
                    scan_id,
                    tenant_id,
                    objetivo["asset_id"],
                    target,
                    f"{_PREFIJO_REPORTE_BARRIDO}: {len(filas)} hallazgo(s) de suplantación sobre {target}",
                    json.dumps(resultado.get("trace_log") or [], default=str),
                    now,
                ),
            )
            hallazgos_normalizados = [
                {
                    "tipo": fila["tipo"],
                    "severidad": fila["severidad"],
                    "endpoint": fila["endpoint"],
                    "confirmado": False,
                    "raw_json": json.dumps(fila["raw_json"], default=str),
                }
                for fila in filas
            ]
            _registrar_findings_con_ciclo_de_vida(
                conn, tenant_id, scan_id, objetivo["asset_id"], hallazgos_normalizados, now
            )
            conn.commit()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo persistir el barrido de marca de %s", target)
            continue
        finally:
            conn.close()

        barridos += 1
        logger.info(
            "Barrido programado de marca: %s -> %d hallazgo(s) (tenant=%s scan_id=%s)",
            target,
            len(filas),
            tenant_id,
            scan_id,
        )

    logger.info(
        "Barrido programado de marca completado: %d/%d objetivo(s) barridos",
        barridos,
        len(objetivos),
    )
    return barridos


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


def run_screenshot_purge_once() -> int:
    """Borra los screenshots de sitios de terceros más viejos que el plazo de retención.

    `POST /antisuplantacion` guarda un PNG del sitio clon por cada dominio
    lookalike enriquecido (`api/main.py::_enriquecer_dominios_clon` ->
    `tools/antisuplantacion.py::capturar_screenshot`), en
    `VIGIA_SCREENSHOT_DIR`. Hasta esta sesión ese directorio solo crecía:
    una copia indefinida del contenido de sitios web de TERCEROS, sin plazo
    ni criterio de borrado. Guardar evidencia de una posible suplantación es
    legítimo y necesario para un takedown; guardarla para siempre no lo es
    -- la minimización y la retención limitada son obligaciones de la Ley
    1581 de 2012 y de cualquier política de uso aceptable que se publique.

    Criterio: antigüedad del archivo (`st_mtime`) contra
    `VIGIA_SCREENSHOT_RETENCION_DIAS` (default 90). Deliberadamente NO se
    cruza contra `findings` -- un hallazgo abierto no justifica conservar la
    captura más allá del plazo, y atar el borrado a la vida del hallazgo
    reintroduciría la retención indefinida por la puerta de atrás.

    Nunca lanza: un archivo que no se puede borrar (bloqueado por otro
    proceso, permisos) se registra y se salta -- este job corre en el
    scheduler de fondo y no debe tumbarlo. Devuelve cuántos archivos borró.
    """
    directorio = os.environ.get("VIGIA_SCREENSHOT_DIR", "data/screenshots")
    try:
        dias = max(0.0, float(os.environ.get("VIGIA_SCREENSHOT_RETENCION_DIAS", "90")))
    except ValueError:
        dias = 90.0

    if not os.path.isdir(directorio):
        return 0

    limite = time.time() - dias * 86400
    borrados = 0
    for nombre in os.listdir(directorio):
        ruta = os.path.join(directorio, nombre)
        try:
            if not os.path.isfile(ruta):
                continue
            if os.stat(ruta).st_mtime >= limite:
                continue
            os.remove(ruta)
            borrados += 1
        except OSError:
            logger.warning("No se pudo purgar el screenshot %s", ruta, exc_info=True)

    if borrados:
        logger.info(
            "Purga de screenshots: %d archivo(s) borrado(s) de %s (retención %.0f día(s))",
            borrados,
            directorio,
            dias,
        )
    return borrados


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

    # Retención de screenshots de terceros: mismo patrón de job periódico que
    # los dos de arriba, con su propio intervalo (default 24h -- una purga por
    # antigüedad no gana nada corriendo más seguido).
    purga_interval_hours = float(os.environ.get("VIGIA_SCREENSHOT_PURGE_INTERVAL_HOURS", "24"))
    _scheduler.add_job(
        run_screenshot_purge_once,
        "interval",
        hours=purga_interval_hours,
        id="vigia_purga_screenshots",
        replace_existing=True,
    )

    # Vigilancia continua de MARCA (ver `run_brand_cycle_once`): el ciclo de
    # recon de arriba corre con `antisuplantacion_habilitado=False`, así que
    # sin este job nadie vuelve a mirar los dominios clon salvo que una
    # persona apriete el botón. Intervalo propio y largo por default (24h):
    # cada corrida sondea infraestructura de terceros, y la cortesía de red
    # importa tanto como la frescura.
    brand_interval_hours = float(os.environ.get("VIGIA_BRAND_CYCLE_HOURS", "24"))
    _scheduler.add_job(
        run_brand_cycle_once,
        "interval",
        hours=brand_interval_hours,
        id="vigia_barrido_marca",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "Scheduler iniciado -- recon recurrente cada %.1fh, escalamiento de SLA cada %.1fh, "
        "purga de screenshots cada %.1fh, barrido de marca cada %.1fh",
        interval_hours,
        sla_interval_hours,
        purga_interval_hours,
        brand_interval_hours,
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
