"""Fase 3 — memoria histórica entre corridas (`agents/memoria.py`).

Se prueba contra la base REAL del proyecto (fixture `test_db`: SQLite temporal
por test, o Postgres real si `VIGIA_PG_TEST_URL` está seteada), insertando
filas con el mismo SQL que usa la app. Nada mockeado: el valor de este módulo
es exactamente que sepa leer el schema que hay.
"""

from __future__ import annotations

import uuid

import pytest

from agents.memoria import contexto_historico
from db.connection import get_conn

TENANT = "tenant-memoria-fase3"
OTRO_TENANT = "tenant-ajeno-fase3"
TARGET = "cliente-memoria.test"


def _sembrar_tenant(conn, tenant_id: str) -> None:
    conn.execute(
        "INSERT INTO tenants (id, slug, name) VALUES (?, ?, ?)",
        (tenant_id, tenant_id, tenant_id),
    )


def _sembrar_scan(conn, tenant_id: str, target: str, estado: str = "completado") -> str:
    scan_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado) "
        "VALUES (?, ?, ?, ?, ?)",
        (scan_id, tenant_id, target, 0, estado),
    )
    return scan_id


def _sembrar_finding(
    conn,
    tenant_id: str,
    scan_id: str,
    tipo: str,
    endpoint: str = "",
    severidad: str = "high",
    estado: str = "descubierto",
) -> str:
    finding_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, "
        "confirmado, raw_json, estado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (finding_id, scan_id, tenant_id, tipo, severidad, endpoint, 1, "{}", estado),
    )
    return finding_id


def _sembrar_transicion(
    conn, tenant_id: str, finding_id: str, anterior: str, nuevo: str, nota: str = ""
) -> None:
    conn.execute(
        "INSERT INTO finding_transiciones (id, finding_id, tenant_id, estado_anterior, "
        "estado_nuevo, nota) VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), finding_id, tenant_id, anterior, nuevo, nota),
    )


@pytest.fixture()
def conn(test_db):
    c = get_conn()
    yield c
    c.commit()
    c.close()


# --- Caso base: cliente nuevo ------------------------------------------------


def test_sin_historial_devuelve_string_vacio(test_db):
    """El estado en el que quedó la base tras Fase 2 es el caso NORMAL."""
    assert contexto_historico(TENANT, TARGET) == ""


def test_argumentos_vacios_no_rompen_ni_consultan(test_db):
    assert contexto_historico("", TARGET) == ""
    assert contexto_historico(TENANT, "") == ""


def test_un_solo_scan_previo_sin_reincidencia_no_inventa_historial(conn):
    _sembrar_tenant(conn, TENANT)
    scan = _sembrar_scan(conn, TENANT, TARGET)
    _sembrar_finding(conn, TENANT, scan, "CSP Header Not Set", "/")
    conn.commit()

    # Un scan solo no es "historial": no hay nada reincidente ni cerrado que
    # contar, así que no se ensucia el prompt con ruido.
    assert contexto_historico(TENANT, TARGET) == ""


# --- Reincidencia: el dato que justifica todo el módulo ----------------------


def test_hallazgo_visto_en_varios_scans_se_reporta_como_reincidente(conn):
    _sembrar_tenant(conn, TENANT)
    for _ in range(3):
        scan = _sembrar_scan(conn, TENANT, TARGET)
        _sembrar_finding(conn, TENANT, scan, "SQL Injection", "/login")
    # Uno que solo apareció una vez: NO debe listarse como reincidente.
    _sembrar_finding(conn, TENANT, scan, "Cookie sin flag Secure", "/perfil")
    conn.commit()

    texto = contexto_historico(TENANT, TARGET)

    assert "REINCIDENTES" in texto
    assert "SQL Injection en /login" in texto
    assert "3 scans distintos" in texto
    assert "Cookie sin flag Secure" not in texto
    assert "escaneado 3 veces" in texto


def test_el_historial_es_texto_legible_no_json(conn):
    _sembrar_tenant(conn, TENANT)
    for _ in range(2):
        scan = _sembrar_scan(conn, TENANT, TARGET)
        _sembrar_finding(conn, TENANT, scan, "XSS reflejado", "/buscar")
    conn.commit()

    texto = contexto_historico(TENANT, TARGET)
    assert texto.startswith("HISTORIAL PREVIO DE ESTE CLIENTE SOBRE")
    assert TARGET in texto
    assert not texto.lstrip().startswith(("{", "["))


# --- Transiciones y descartes del cliente ------------------------------------


def test_transiciones_recientes_aparecen_con_su_nota(conn):
    _sembrar_tenant(conn, TENANT)
    scan_a = _sembrar_scan(conn, TENANT, TARGET)
    scan_b = _sembrar_scan(conn, TENANT, TARGET)
    _sembrar_finding(conn, TENANT, scan_a, "Directorio listable", "/backups")
    f2 = _sembrar_finding(conn, TENANT, scan_b, "Directorio listable", "/backups")
    _sembrar_transicion(conn, TENANT, f2, "descubierto", "en_progreso", "asignado a infra")
    conn.commit()

    texto = contexto_historico(TENANT, TARGET)
    assert "descubierto a en_progreso" in texto
    assert "asignado a infra" in texto


def test_riesgo_aceptado_se_reporta_como_ya_descartado(conn):
    _sembrar_tenant(conn, TENANT)
    scan_a = _sembrar_scan(conn, TENANT, TARGET)
    scan_b = _sembrar_scan(conn, TENANT, TARGET)
    _sembrar_finding(conn, TENANT, scan_a, "Servidor expone versión", "/")
    _sembrar_finding(
        conn, TENANT, scan_b, "Servidor expone versión", "/", estado="aceptado_riesgo"
    )
    conn.commit()

    texto = contexto_historico(TENANT, TARGET)
    assert "YA descartó" in texto
    assert "riesgo aceptado" in texto


def test_nota_de_falso_positivo_se_detecta_sin_usar_LIKE(conn):
    """`db/connection.py` traduce `?` -> `%s` con un replace ingenuo, así que
    un `LIKE '%...%'` rompería contra Postgres. El filtrado va en Python."""
    _sembrar_tenant(conn, TENANT)
    scan_a = _sembrar_scan(conn, TENANT, TARGET)
    scan_b = _sembrar_scan(conn, TENANT, TARGET)
    _sembrar_finding(conn, TENANT, scan_a, "Panel admin expuesto", "/admin")
    f2 = _sembrar_finding(conn, TENANT, scan_b, "Panel admin expuesto", "/admin")
    _sembrar_transicion(
        conn, TENANT, f2, "triage", "cerrado", "Es un Falso Positivo, ese panel es interno"
    )
    conn.commit()

    texto = contexto_historico(TENANT, TARGET)
    assert "falso positivo" in texto.lower()
    assert "ese panel es interno" in texto


# --- Aislamiento -------------------------------------------------------------


def test_no_filtra_historial_de_otro_tenant(conn):
    _sembrar_tenant(conn, TENANT)
    _sembrar_tenant(conn, OTRO_TENANT)
    for _ in range(3):
        scan = _sembrar_scan(conn, OTRO_TENANT, TARGET)
        _sembrar_finding(conn, OTRO_TENANT, scan, "Secreto en repo", "/config")
    conn.commit()

    assert contexto_historico(TENANT, TARGET) == ""
    assert "Secreto en repo" in contexto_historico(OTRO_TENANT, TARGET)


def test_no_mezcla_historial_de_otro_target_del_mismo_tenant(conn):
    _sembrar_tenant(conn, TENANT)
    for _ in range(2):
        scan = _sembrar_scan(conn, TENANT, "otro-dominio.test")
        _sembrar_finding(conn, TENANT, scan, "RCE en uploader", "/upload")
    conn.commit()

    assert contexto_historico(TENANT, TARGET) == ""
    assert "RCE en uploader" in contexto_historico(TENANT, "otro-dominio.test")


# --- Integración con los nodos que lo consumen -------------------------------


def test_priorizacion_inyecta_el_historial_en_el_prompt(monkeypatch):
    import agents.priorizacion as priorizacion

    capturado = {}

    def _fake(system, user):
        capturado["user"] = user
        return "[]"

    monkeypatch.setattr(priorizacion, "call_claude", _fake)
    priorizacion.node(
        {
            "verified_findings": [{"confirmado": True, "objetivo": "x"}],
            "contexto_historico": "HISTORIAL PREVIO: esto ya se reportó 4 veces.",
        }
    )
    assert "esto ya se reportó 4 veces" in capturado["user"]


def test_priorizacion_sin_historial_no_cambia_el_prompt(monkeypatch):
    """Propiedad aditiva: sin memoria, el prompt es el de antes de Fase 3."""
    import agents.priorizacion as priorizacion

    capturados = []
    monkeypatch.setattr(
        priorizacion, "call_claude", lambda s, u: capturados.append(u) or "[]"
    )
    base = {"verified_findings": [{"confirmado": True, "objetivo": "x"}]}
    priorizacion.node(base)
    priorizacion.node({**base, "contexto_historico": ""})

    assert capturados[0] == capturados[1]
    assert capturados[0].startswith("Contexto de negocio del cliente:")


def test_reporteria_inyecta_el_historial_en_el_prompt(monkeypatch):
    import agents.reporteria as reporteria

    capturado = {}
    monkeypatch.setattr(
        reporteria,
        "call_claude",
        lambda s, u: capturado.setdefault("user", u) or "reporte",
    )
    reporteria.node(
        {"target": "x.test", "contexto_historico": "HISTORIAL PREVIO: hallazgo abierto hace 6 scans."}
    )
    assert "hallazgo abierto hace 6 scans" in capturado["user"]
    assert "REINCIDENTE" in capturado["user"]


def test_reporteria_sin_historial_no_cambia_el_prompt(monkeypatch):
    import agents.reporteria as reporteria

    capturados = []
    monkeypatch.setattr(
        reporteria, "call_claude", lambda s, u: capturados.append(u) or "reporte"
    )
    reporteria.node({"target": "x.test"})
    reporteria.node({"target": "x.test", "contexto_historico": "   "})

    assert capturados[0] == capturados[1]
    assert capturados[0].startswith("Datos del pipeline")
