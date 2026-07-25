"""Notificaciones y alertas — Fase 5 (openspec/changes/fase5-notificaciones-alertas).

Hoy Vigia detecta pero el cliente no se entera hasta que abre el panel — sin
alertas activas no es "vigilancia". Este módulo cierra ese lazo: un
despachador central `enviar_notificacion(...)` que evalúa `notification_rules`
por tenant+evento, envía por email (`smtplib`, stdlib — sin dependencias
nuevas) y registra idempotencia sobre `notifications`, ambas tablas ya
existentes desde `fase0-fundaciones-datos`.

Tres call-sites lo invocan (ver design.md, Data Flow):
- `api/main.py::_registrar_findings_con_ciclo_de_vida` -- finding crítico nuevo.
- `api/certstream_listener.py::registrar_finding_certstream` -- dominio clon.
- `api/scheduler.py::run_sla_escalation_once` -- escalamiento de SLA (75%/vencido).

Envío best-effort SIEMPRE: un fallo de SMTP (host caído, credenciales malas,
timeout, no configurado) NUNCA debe propagarse al path que originó el evento
(persistencia de findings, ciclo del scheduler) -- ver spec.md, Requirement
"Envío best-effort, aislado del flujo llamante". `enviar_notificacion` atrapa
toda excepción y jamás la relanza.

Idempotencia (design.md, Open Question #1, Opción A elegida): sin columna
nueva en `notifications`, el `recurso_id` (típicamente `finding_id`, o
`finding_id` + umbral de SLA) se codifica dentro de la columna `evento` como
`"{evento}:{recurso_id}"` -- ej. `"hallazgo_critico_nuevo:f123"` o
`"sla_escalamiento:75pct:f123"`. La clave de idempotencia real es
`(tenant_id, evento_compuesto, destino)`: dos reglas con destinos distintos
para el mismo evento/recurso se tratan como notificaciones independientes
(cada una se envía y se registra por separado), pero la MISMA regla nunca
re-envía para el mismo recurso. El match de `notification_rules` en cambio
usa solo la porción base del evento (antes del primer `:`), para que
`sla_escalamiento:75pct` y `sla_escalamiento:vencido` compartan una sola
regla/destino configurados a nivel `sla_escalamiento` (ver design.md,
Interfaces / Contracts).
"""

from __future__ import annotations

import logging
import os
import smtplib
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage

logger = logging.getLogger("vigia.notificaciones")

CANALES_SOPORTADOS = ("email",)


def _evento_base(evento: str) -> str:
    """`"sla_escalamiento:75pct"` -> `"sla_escalamiento"`; `"hallazgo_critico_nuevo"` -> sin cambio."""
    return evento.split(":", 1)[0]


def _clave_notificacion(evento: str, recurso_id: str) -> str:
    """Codifica el recurso dentro de `evento` -- ver docstring del módulo, Opción A."""
    return f"{evento}:{recurso_id}"


def _config_smtp() -> dict | None:
    """Lee la config SMTP de variables de entorno (mismo patrón que
    `VIGIA_SCAN_INTERVAL_HOURS`/`CORS_ORIGINS` -- ver design.md, Architecture
    Decisions). Si falta `VIGIA_SMTP_HOST`, devuelve `None` -- el envío se
    omite limpio (loguea, no falla; ver spec.md, escenario "SMTP no
    configurado")."""
    host = os.environ.get("VIGIA_SMTP_HOST")
    if not host:
        return None
    tls = os.environ.get("VIGIA_SMTP_TLS", "true").strip().lower() not in ("0", "false", "no")
    user = os.environ.get("VIGIA_SMTP_USER") or None
    return {
        "host": host,
        "port": int(os.environ.get("VIGIA_SMTP_PORT", "587")),
        "user": user,
        "password": os.environ.get("VIGIA_SMTP_PASSWORD") or None,
        "from_addr": os.environ.get("VIGIA_SMTP_FROM") or user or "vigia@localhost",
        "tls": tls,
    }


def _enviar_email_smtp(destino: str, asunto: str, cuerpo: str) -> bool:
    """Envía un email de texto plano vía `smtplib`. Nunca lanza -- retorna
    `False` ante cualquier problema (sin config, host caído, credenciales
    malas, timeout), `True` solo si el servidor SMTP aceptó el mensaje."""
    config = _config_smtp()
    if config is None:
        logger.info("SMTP no configurado (VIGIA_SMTP_HOST ausente) -- omitiendo envío a %s", destino)
        return False

    try:
        mensaje = EmailMessage()
        mensaje["Subject"] = asunto
        mensaje["From"] = config["from_addr"]
        mensaje["To"] = destino
        mensaje.set_content(cuerpo)

        with smtplib.SMTP(config["host"], config["port"], timeout=10) as server:
            if config["tls"]:
                server.starttls()
            if config["user"] and config["password"]:
                server.login(config["user"], config["password"])
            server.send_message(mensaje)
        return True
    except Exception:
        logger.exception("Fallo enviando notificación por email a %s", destino)
        return False


def _reglas_activas(conn, tenant_id: str, evento: str) -> list[dict]:
    """Reglas activas del tenant para el evento base (ignora sufijo `:umbral`),
    solo canal `email` (único soportado en v1 -- Slack/webhook quedan
    reservados en el esquema, ver spec.md)."""
    from db.connection import dict_from_row

    evento_base = _evento_base(evento)
    rows = conn.execute(
        "SELECT * FROM notification_rules WHERE tenant_id = ? AND evento = ? AND canal = 'email' AND is_active = 1",
        (tenant_id, evento_base),
    ).fetchall()
    return [dict_from_row(r) for r in rows]


def _ya_enviado(conn, tenant_id: str, clave_evento: str, destino: str) -> bool:
    """`True` si ya existe una fila en `notifications` para esta clave exacta
    (tenant+evento compuesto+destino) -- sin importar si el intento anterior
    tuvo éxito o falló (ver design.md: el registro del intento ES lo que
    evita reintentos indefinidos)."""
    row = conn.execute(
        "SELECT 1 FROM notifications WHERE tenant_id = ? AND evento = ? AND destino = ?",
        (tenant_id, clave_evento, destino),
    ).fetchone()
    return row is not None


def enviar_notificacion(
    conn,
    tenant_id: str,
    evento: str,
    recurso_id: str,
    asunto: str,
    cuerpo: str,
) -> bool:
    """Despachador central: reglas activas -> idempotencia -> envío SMTP -> registro.

    Best-effort SIEMPRE -- nunca lanza. Devuelve `True` solo si se envió (al
    menos) un email nuevo; `False` en cualquier otro caso (sin regla, ya
    enviado, SMTP no configurado/falló). El path llamante (persistencia de
    findings, scheduler) nunca debe cambiar de comportamiento según el valor
    de retorno más allá de logging opcional -- el contrato real es "esto no
    rompe nada".
    """
    try:
        reglas = _reglas_activas(conn, tenant_id, evento)
        if not reglas:
            return False

        clave_evento = _clave_notificacion(evento, recurso_id)
        enviado_alguno = False
        for regla in reglas:
            destino = regla["destino"]
            if _ya_enviado(conn, tenant_id, clave_evento, destino):
                continue

            enviado_ok = _enviar_email_smtp(destino, asunto, cuerpo)
            enviado_en = datetime.now(timezone.utc).isoformat() if enviado_ok else None
            conn.execute(
                """
                INSERT INTO notifications (id, tenant_id, canal, destino, evento, enviado_en)
                VALUES (?, ?, 'email', ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    tenant_id,
                    destino,
                    clave_evento,
                    enviado_en,
                ),
            )
            conn.commit()
            enviado_alguno = enviado_alguno or enviado_ok

        return enviado_alguno
    except Exception:
        logger.exception(
            "enviar_notificacion falló de forma inesperada -- tenant=%s evento=%s recurso=%s (best-effort, no se propaga)",
            tenant_id,
            evento,
            recurso_id,
        )
        return False
