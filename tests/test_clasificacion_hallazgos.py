"""Fase 7 (señal vs. ruido) -- clasificación determinista por CLASE.

Cubre las tres capas del cambio:

1. `tools/clasificacion.py` puro, contra hallazgos con la FORMA REAL que
   producen ZAP baseline, la API de ZAP, Nuclei, Semgrep y
   `agents/antisuplantacion.py` (no dicts inventados).
2. La persistencia: `findings.clase` se puebla al insertar, y los hallazgos
   históricos (`clase IS NULL`) se derivan en lectura sin romperse.
3. El SLA moderado por clase (`_calcular_sla_due_at`) -- que una cabecera
   faltante no arrastre un plazo de 7 días "crítico".
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from api.main import DEFAULT_SLA_DIAS, _calcular_sla_due_at, _registrar_findings_con_ciclo_de_vida
from db.connection import get_conn
from tools.clasificacion import (
    CLASE_AMENAZA_EXTERNA,
    CLASE_EXPLOTABLE,
    CLASE_HIGIENE,
    CLASE_INFORMATIVO,
    clasificar_fila_finding,
    clasificar_finding,
)

# ---------------------------------------------------------------------------
# 1. Clasificador puro
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tipo, clase_esperada",
    [
        # --- El caso que motivó todo el cambio: un SQLi real y un CSP
        # faltante NO pueden verse igual en la lista. ---
        ("SQL Injection", CLASE_EXPLOTABLE),
        ("CSP: Wildcard Directive", CLASE_HIGIENE),
        ("Content Security Policy (CSP) Header Not Set", CLASE_HIGIENE),
        # --- Otras clases de ataque reales (nombres de alerta reales). ---
        ("SQL Injection - SQLite", CLASE_EXPLOTABLE),
        ("Cross Site Scripting (Reflected)", CLASE_EXPLOTABLE),
        ("Cross Site Scripting (DOM Based)", CLASE_EXPLOTABLE),
        ("Path Traversal", CLASE_EXPLOTABLE),
        ("Remote OS Command Injection", CLASE_EXPLOTABLE),
        ("Server Side Request Forgery", CLASE_EXPLOTABLE),
        ("Remote Code Execution - CVE-2012-1823", CLASE_EXPLOTABLE),
        # --- Ruido de configuración de ZAP baseline (nombres reales). ---
        ("Missing Anti-clickjacking Header", CLASE_HIGIENE),
        ("X-Frame-Options Header Not Set", CLASE_HIGIENE),
        ("X-Content-Type-Options Header Missing", CLASE_HIGIENE),
        ("Strict-Transport-Security Header Not Set", CLASE_HIGIENE),
        ("Cookie No HttpOnly Flag", CLASE_HIGIENE),
        ("Cookie Without Secure Flag", CLASE_HIGIENE),
        ("Cookie without SameSite Attribute", CLASE_HIGIENE),
        ("Permissions Policy Header Not Set", CLASE_HIGIENE),
        ("Server Leaks Version Information via \"Server\" HTTP Response Header Field", CLASE_HIGIENE),
        ("Re-examine Cache-control Directives", CLASE_HIGIENE),
        ("Absence of Anti-CSRF Tokens", CLASE_HIGIENE),
        ("Deprecated Feature Policy Header Set", CLASE_HIGIENE),
        # --- Puro reporte. ---
        ("Modern Web Application", CLASE_INFORMATIVO),
        ("Timestamp Disclosure - Unix", CLASE_INFORMATIVO),
        ("Information Disclosure - Suspicious Comments", CLASE_INFORMATIVO),
        ("User Agent Fuzzer", CLASE_INFORMATIVO),
        # --- Suplantación de marca: ni higiene ni vuln propia. ---
        ("dominio_variante", CLASE_AMENAZA_EXTERNA),
        ("dominio_variante_certstream", CLASE_AMENAZA_EXTERNA),
        ("perfil_red_social", CLASE_AMENAZA_EXTERNA),
    ],
)
def test_clasificacion_por_nombre_de_alerta_real(tipo, clase_esperada):
    assert clasificar_finding(tipo) == clase_esperada


def test_nuclei_con_cve_es_explotable():
    """Un hallazgo de Nuclei que cita un CVE es una vulnerabilidad concreta en
    un componente real, no una buena práctica ausente -- aunque el nombre de
    la plantilla suene a mero descubrimiento ("... Detection")."""
    raw_nuclei = {
        "template-id": "CVE-2021-44228",
        "info": {
            "name": "Apache Log4j2 JNDI RCE Detection",
            "severity": "critical",
            "classification": {"cve-id": ["CVE-2021-44228"], "cwe-id": ["cwe-502"]},
        },
        "matched-at": "https://ejemplo.test/",
    }
    assert clasificar_finding("CVE-2021-44228", raw_nuclei, severidad="critical") == CLASE_EXPLOTABLE


def test_nuclei_con_cve_sin_palabra_clave_reconocible_sigue_siendo_explotable():
    """Sin patrón de nombre y sin CWE: el CVE solo ya alcanza. Este es el caso
    que se rompería si el paso de "patrones informativos" corriera antes que
    el de CVE (muchas plantillas se llaman literalmente `CVE-XXXX-YYYY`)."""
    raw = {"template-id": "CVE-2023-99999", "info": {"name": "Producto X 1.2.3", "severity": "high"}}
    assert clasificar_finding("CVE-2023-99999", raw, severidad="high") == CLASE_EXPLOTABLE


def test_confirmado_via_exploitdb_gana_sobre_cualquier_patron_de_nombre():
    """Si `agents/verificacion.py` encontró PoC pública para el CVE, esa es la
    evidencia más fuerte que este sistema produce: ningún patrón de texto
    debería poder degradarla a higiene."""
    clase = clasificar_finding(
        "X-Frame-Options Header Not Set",
        {"cveid": "CVE-2020-1234"},
        severidad="medium",
        confirmado=True,
    )
    assert clase == CLASE_EXPLOTABLE

    clase_por_estado = clasificar_finding(
        "X-Frame-Options Header Not Set",
        None,
        severidad="medium",
        estado_verificacion="confirmado_via_exploitdb",
    )
    assert clase_por_estado == CLASE_EXPLOTABLE


def test_zap_api_alert_usa_el_campo_name_no_solo_el_tipo():
    """Forma real de `tools/zap_api.py::get_alerts()` -- una fila por instancia
    con `name`/`risk`/`cweid` de nivel superior."""
    alerta = {
        "name": "SQL Injection",
        "risk": "High",
        "cweid": "89",
        "url": "https://ejemplo.test/rest/products/search?q=1",
    }
    assert clasificar_finding("desconocido", alerta, severidad="high") == CLASE_EXPLOTABLE


def test_cwe_declarado_decide_cuando_el_nombre_no_dice_nada():
    """CWE-89 (SQLi) por campo explícito, con un nombre que no matchea ningún
    patrón: la clasificación no puede depender solo del texto."""
    assert clasificar_finding("Regla interna 4821", {"cweid": 89}, severidad="high") == CLASE_EXPLOTABLE
    # CWE-1021 (clickjacking) por el mismo camino, del lado de higiene.
    assert clasificar_finding("Regla interna 9911", {"cweid": "1021"}, severidad="medium") == CLASE_HIGIENE


def test_cwe_solo_se_lee_de_campos_explicitos_no_del_texto_libre():
    """Un CWE citado de paso en una referencia no debe reclasificar nada --
    si bastara con escanear el JSON entero, cualquier alerta de ZAP con un
    link a cwe.mitre.org en `reference` cambiaría de clase."""
    raw = {
        "name": "Regla interna sin señal",
        "reference": "https://cwe.mitre.org/data/definitions/89.html",
    }
    # Sin CWE explícito ni CVE ni patrón: cae al fallback por severidad, NO a
    # explotable "por el 89 del link".
    assert clasificar_finding("Regla interna sin señal", raw, severidad="info") == CLASE_INFORMATIVO


def test_semgrep_metadata_cwe_es_explotable():
    raw_semgrep = {
        "check_id": "python.lang.security.audit.dangerous-subprocess-use",
        "extra": {
            "severity": "ERROR",
            "message": "Detected subprocess function with user input",
            "metadata": {"cwe": ["CWE-78: Improper Neutralization of Special Elements used in an OS Command"]},
        },
    }
    assert clasificar_finding("dangerous-subprocess-use", raw_semgrep, severidad="high") == CLASE_EXPLOTABLE


def test_nombre_con_ataque_real_gana_sobre_palabra_de_higiene():
    """"Cookie" es un patrón de higiene y "XSS" uno de ataque real: cuando
    conviven en el mismo nombre debe ganar el ataque, nunca al revés (errar
    hacia esconder algo real es el único error inaceptable acá)."""
    assert clasificar_finding("Session Cookie Theft via Stored XSS") == CLASE_EXPLOTABLE


def test_fallback_desconocido_no_esconde_nada_como_higiene():
    """Un hallazgo sin patrón, sin CWE y sin CVE con severidad media o peor
    queda ARRIBA (visible), no colapsado en el bloque de higiene."""
    assert clasificar_finding("Regla propietaria XYZ-1", severidad="high") == CLASE_EXPLOTABLE
    assert clasificar_finding("Regla propietaria XYZ-1", severidad="info") == CLASE_INFORMATIVO


def test_clasificacion_es_determinista_entre_llamadas():
    """Requisito de producto, no de implementación: el cliente tiene que poder
    volver mañana y ver la misma etiqueta. Sin LLM de por medio, esto es
    trivialmente cierto -- el test lo deja congelado como contrato."""
    raw = {"name": "SQL Injection", "cweid": "89"}
    resultados = {clasificar_finding("SQL Injection", raw, severidad="high") for _ in range(10)}
    assert resultados == {CLASE_EXPLOTABLE}


# ---------------------------------------------------------------------------
# 2. Derivación en lectura para filas ya persistidas
# ---------------------------------------------------------------------------


def test_fila_con_clase_guardada_se_respeta_sin_recalcular():
    fila = {"tipo": "SQL Injection", "severidad": "high", "confirmado": 0, "clase": CLASE_HIGIENE}
    assert clasificar_fila_finding(fila) == CLASE_HIGIENE


def test_fila_historica_sin_clase_se_deriva_del_raw_json():
    """`clase IS NULL` = fila guardada antes de esta migración. Se deriva con
    el mismo criterio, sin backfill destructivo."""
    fila = {
        "tipo": "desconocido",
        "severidad": "high",
        "confirmado": 0,
        "clase": None,
        "raw_json": json.dumps({"name": "Cross Site Scripting (Reflected)", "risk": "High"}),
    }
    assert clasificar_fila_finding(fila) == CLASE_EXPLOTABLE


def test_fila_con_raw_json_corrupto_no_revienta():
    fila = {"tipo": "CSP: Wildcard Directive", "severidad": "medium", "clase": None, "raw_json": "{no es json"}
    assert clasificar_fila_finding(fila) == CLASE_HIGIENE


# ---------------------------------------------------------------------------
# 3. Persistencia + SLA moderado por clase
# ---------------------------------------------------------------------------


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123") -> str:
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    resp_me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    return resp_me.json()["tenant_id"], token


def test_higiene_critical_no_hereda_el_plazo_de_7_dias(client):
    """Un SLA de 7 días para una cabecera faltante no es un compromiso serio:
    es una fábrica de vencimientos que entrena al cliente a ignorar el rojo."""
    tenant_id, _ = _registrar_tenant(client, "Tenant Clase SLA", "clasesla@clase.test")
    now = datetime.now(timezone.utc).isoformat()

    conn = get_conn()
    try:
        sla_explotable = _calcular_sla_due_at(conn, tenant_id, "critical", now, CLASE_EXPLOTABLE)
        sla_higiene = _calcular_sla_due_at(conn, tenant_id, "critical", now, CLASE_HIGIENE)
        sla_informativo = _calcular_sla_due_at(conn, tenant_id, "critical", now, CLASE_INFORMATIVO)
        sla_sin_clase = _calcular_sla_due_at(conn, tenant_id, "critical", now)
    finally:
        conn.close()

    assert datetime.fromisoformat(sla_explotable) == datetime.fromisoformat(now) + timedelta(
        days=DEFAULT_SLA_DIAS["critical"]
    )
    # Higiene crítica cae al plazo de `medium`, no al de `critical`.
    assert datetime.fromisoformat(sla_higiene) == datetime.fromisoformat(now) + timedelta(
        days=DEFAULT_SLA_DIAS["medium"]
    )
    assert sla_informativo is None
    # Sin clase (llamador viejo) -> comportamiento histórico intacto.
    assert sla_sin_clase == sla_explotable


def test_amenaza_externa_conserva_el_plazo_estricto(client):
    tenant_id, _ = _registrar_tenant(client, "Tenant Clase Marca", "clasemarca@clase.test")
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        sla = _calcular_sla_due_at(conn, tenant_id, "high", now, CLASE_AMENAZA_EXTERNA)
    finally:
        conn.close()
    assert datetime.fromisoformat(sla) == datetime.fromisoformat(now) + timedelta(
        days=DEFAULT_SLA_DIAS["high"]
    )


def test_insercion_puebla_clase_y_findings_la_expone(client):
    """Extremo a extremo: se insertan un SQLi y un CSP faltante con la misma
    severidad y `GET /findings` los devuelve con clases distintas."""
    tenant_id, token = _registrar_tenant(client, "Tenant Clase E2E", "clasee2e@clase.test")
    now = datetime.now(timezone.utc).isoformat()
    scan_id = str(uuid.uuid4())

    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO scans (id, tenant_id, asset_id, target, autorizacion_firmada, estado, trace_log_json)
            VALUES (?, ?, NULL, 'ejemplo.test', 1, 'completado', '[]')
            """,
            (scan_id, tenant_id),
        )
        _registrar_findings_con_ciclo_de_vida(
            conn,
            tenant_id,
            scan_id,
            None,
            [
                {
                    "tipo": "SQL Injection",
                    "severidad": "high",
                    "endpoint": "https://ejemplo.test/buscar",
                    "confirmado": False,
                    "raw_json": json.dumps({"name": "SQL Injection", "risk": "High", "cweid": "89"}),
                },
                {
                    "tipo": "Content Security Policy (CSP) Header Not Set",
                    "severidad": "high",
                    "endpoint": "https://ejemplo.test/",
                    "confirmado": False,
                    "raw_json": json.dumps(
                        {"name": "Content Security Policy (CSP) Header Not Set", "risk": "High"}
                    ),
                },
            ],
            now,
        )
        conn.commit()

        filas = {
            f["tipo"]: f["clase"]
            for f in conn.execute(
                "SELECT tipo, clase FROM findings WHERE tenant_id = ?", (tenant_id,)
            ).fetchall()
        }
    finally:
        conn.close()

    assert filas["SQL Injection"] == CLASE_EXPLOTABLE
    assert filas["Content Security Policy (CSP) Header Not Set"] == CLASE_HIGIENE

    resp = client.get("/findings", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    por_tipo = {f["tipo"]: f["clase"] for f in resp.json()}
    assert por_tipo["SQL Injection"] == CLASE_EXPLOTABLE
    assert por_tipo["Content Security Policy (CSP) Header Not Set"] == CLASE_HIGIENE

    # Y el SLA quedó moderado en la fila de higiene, no heredado de `high`.
    conn = get_conn()
    try:
        slas = {
            f["tipo"]: f["sla_due_at"]
            for f in conn.execute(
                "SELECT tipo, sla_due_at FROM findings WHERE tenant_id = ?", (tenant_id,)
            ).fetchall()
        }
    finally:
        conn.close()
    assert datetime.fromisoformat(slas["SQL Injection"]) < datetime.fromisoformat(
        slas["Content Security Policy (CSP) Header Not Set"]
    )


def test_finding_historico_sin_clase_se_expone_derivado(client):
    """Fila escrita "a mano" sin `clase` (simula una guardada antes de esta
    migración): `GET /findings` y `GET /findings/{id}` igual la clasifican."""
    tenant_id, token = _registrar_tenant(client, "Tenant Clase Vieja", "clasevieja@clase.test")
    scan_id = str(uuid.uuid4())
    finding_id = str(uuid.uuid4())

    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO scans (id, tenant_id, asset_id, target, autorizacion_firmada, estado, trace_log_json)
            VALUES (?, ?, NULL, 'viejo.test', 1, 'completado', '[]')
            """,
            (scan_id, tenant_id),
        )
        conn.execute(
            """
            INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json)
            VALUES (?, ?, ?, 'Missing Anti-clickjacking Header', 'medium', 'https://viejo.test/', 0, '{}')
            """,
            (finding_id, scan_id, tenant_id),
        )
        conn.commit()
        clase_en_db = conn.execute(
            "SELECT clase FROM findings WHERE id = ?", (finding_id,)
        ).fetchone()["clase"]
    finally:
        conn.close()

    assert clase_en_db is None  # la fila histórica sigue con NULL, no se reescribe

    headers = {"Authorization": f"Bearer {token}"}
    lista = client.get("/findings", headers=headers).json()
    assert lista[0]["clase"] == CLASE_HIGIENE
    detalle = client.get(f"/findings/{finding_id}", headers=headers).json()
    assert detalle["clase"] == CLASE_HIGIENE


def test_kpis_cuentan_hallazgos_abiertos_por_clase(client):
    tenant_id, token = _registrar_tenant(client, "Tenant Clase KPI", "clasekpi@clase.test")
    now = datetime.now(timezone.utc).isoformat()
    scan_id = str(uuid.uuid4())

    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO scans (id, tenant_id, asset_id, target, autorizacion_firmada, estado, trace_log_json)
            VALUES (?, ?, NULL, 'kpi.test', 1, 'completado', '[]')
            """,
            (scan_id, tenant_id),
        )
        _registrar_findings_con_ciclo_de_vida(
            conn,
            tenant_id,
            scan_id,
            None,
            [
                {
                    "tipo": "SQL Injection",
                    "severidad": "critical",
                    "endpoint": "/a",
                    "confirmado": False,
                    "raw_json": "{}",
                },
                {
                    "tipo": "X-Frame-Options Header Not Set",
                    "severidad": "medium",
                    "endpoint": "/b",
                    "confirmado": False,
                    "raw_json": "{}",
                },
                {
                    "tipo": "Cookie No HttpOnly Flag",
                    "severidad": "low",
                    "endpoint": "/c",
                    "confirmado": False,
                    "raw_json": "{}",
                },
            ],
            now,
        )
        conn.commit()
    finally:
        conn.close()

    kpis = client.get("/kpis", headers={"Authorization": f"Bearer {token}"}).json()
    por_clase = {c["clase"]: c["total"] for c in kpis["hallazgos_abiertos_por_clase"]}
    # Siempre vienen las 4 clases, con 0 cuando no hay ninguna.
    assert set(por_clase) == {
        CLASE_EXPLOTABLE,
        CLASE_AMENAZA_EXTERNA,
        CLASE_HIGIENE,
        CLASE_INFORMATIVO,
    }
    assert por_clase[CLASE_EXPLOTABLE] == 1
    assert por_clase[CLASE_HIGIENE] == 2
    assert por_clase[CLASE_AMENAZA_EXTERNA] == 0


def test_higiene_critica_no_dispara_notificacion(client, monkeypatch):
    """Un correo urgente por una cabecera faltante es cómo un producto de
    seguridad se gana que le pongan filtro a sus notificaciones."""
    tenant_id, _ = _registrar_tenant(client, "Tenant Clase Notif", "clasenotif@clase.test")
    enviadas: list[str] = []
    monkeypatch.setattr(
        "api.main.enviar_notificacion",
        lambda conn, tid, evento, fid, asunto, cuerpo: enviadas.append(asunto),
    )

    now = datetime.now(timezone.utc).isoformat()
    scan_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO scans (id, tenant_id, asset_id, target, autorizacion_firmada, estado, trace_log_json)
            VALUES (?, ?, NULL, 'notif.test', 1, 'completado', '[]')
            """,
            (scan_id, tenant_id),
        )
        _registrar_findings_con_ciclo_de_vida(
            conn,
            tenant_id,
            scan_id,
            None,
            [
                {
                    "tipo": "Content Security Policy (CSP) Header Not Set",
                    "severidad": "critical",
                    "endpoint": "/x",
                    "confirmado": False,
                    "raw_json": "{}",
                },
                {
                    "tipo": "SQL Injection",
                    "severidad": "critical",
                    "endpoint": "/y",
                    "confirmado": False,
                    "raw_json": "{}",
                },
            ],
            now,
        )
        conn.commit()
    finally:
        conn.close()

    assert len(enviadas) == 1
    assert "SQL Injection" in enviadas[0]
