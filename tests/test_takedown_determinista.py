"""Borrador de takedown determinista (`tools/takedown.py`) y la sección
"Exposición de marca" del export ejecutivo (`tools/report_export.py`).

Contexto: el panel "Protección de marca" mostraba como "Borrador de takedown"
el campo `analisis_claude` -- un único texto del LOTE de señales, repetido en
cada fila, y `None` cuando el despliegue no tenía API key de LLM. Un cliente
sin LLM configurado recibía un hallazgo sin ninguna acción posible. Estos
tests fijan el comportamiento nuevo: borrador por dominio, con datos reales,
sin depender del LLM ni de la red (RDAP se inyecta, ver
`conftest::_sin_rdap_real_por_defecto`).
"""

from __future__ import annotations

import json
import uuid

from db.connection import get_conn
from tools.report_export import construir_markdown_ejecutivo
from tools.takedown import _vcard_a_dict, generar_borrador_takedown

SEÑAL_BASE = {
    "tipo": "dominio_variante",
    "fuzzer": "bitsquatting",
    "dominio": "mibanko.test",
    "dns_a": ["203.0.113.5"],
    "dns_ns": ["ns1.hostbarato.test"],
    "registrado": True,
    "es_defensivo": False,
    "tiene_correo": True,
    "scoring": {"dns_a": True, "mx": True, "ssl": True, "http": False},
    "score": 3,
    "ssl_not_before": "2026-07-01T00:00:00+00:00",
}


# ---------------------------------------------------------------------------
# tools/takedown.py
# ---------------------------------------------------------------------------


def test_borrador_usa_el_contacto_de_abuso_real_del_registrador():
    texto = generar_borrador_takedown(
        dominio_clon="mibanko.test",
        dominio_legitimo="mibanco.test",
        señal=SEÑAL_BASE,
        rdap={
            "registrador": "Registrador Ejemplo Inc",
            "abuse_email": "abuse@registrador-ejemplo.test",
            "registrado_en": "2026-06-28T10:00:00Z",
        },
    )
    assert "abuse@registrador-ejemplo.test" in texto
    assert "Registrador Ejemplo Inc" in texto
    assert "2026-06-28T10:00:00Z" in texto
    assert "mibanko.test" in texto and "mibanco.test" in texto


def test_borrador_sin_rdap_sigue_siendo_usable_y_no_inventa_destinatario():
    """Si RDAP no responde (TLD sin servidor, red caída, dominio recién
    registrado), la carta debe generarse igual y DECIR que no hay contacto
    publicado -- nunca inventar una dirección de abuso."""
    texto = generar_borrador_takedown(
        dominio_clon="mibanko.test", dominio_legitimo="mibanco.test", señal=SEÑAL_BASE, rdap={}
    )
    assert "no publicado por el registrador" in texto
    assert "@" not in texto.split("SOLICITUD")[0].split("\n")[0].replace("Para:", "")
    # La evidencia técnica que SÍ tenemos sigue estando.
    assert "203.0.113.5" in texto
    assert "Solicitamos la suspensión del dominio mibanko.test" in texto


def test_borrador_no_depende_del_llm_ni_menciona_demo():
    """La suite corre con `_sin_llm_real_por_defecto`: si este texto se
    genera igual, es porque no pasa por ningún LLM."""
    texto = generar_borrador_takedown(
        dominio_clon="mibanko.test", dominio_legitimo="mibanco.test", señal=SEÑAL_BASE
    )
    assert texto.strip()
    for prohibido in ("demo", "ficticio", "reproducida", "mock", "lorem"):
        assert prohibido not in texto.lower()


def test_borrador_destaca_el_riesgo_de_correo_solo_cuando_hay_mx():
    con_mx = generar_borrador_takedown(
        dominio_clon="a.test", dominio_legitimo="b.test", señal=SEÑAL_BASE
    )
    assert "servidores de correo (MX) configurados" in con_mx

    señal_sin_mx = {**SEÑAL_BASE, "tiene_correo": False, "scoring": {"dns_a": True}}
    sin_mx = generar_borrador_takedown(
        dominio_clon="a.test", dominio_legitimo="b.test", señal=señal_sin_mx
    )
    assert "servidores de correo (MX) configurados" not in sin_mx


def test_borrador_con_señal_vacia_no_revienta():
    """Degradación con gracia: un hallazgo antiguo sin las claves nuevas
    (`scoring`, `dns_a`, …) debe producir una carta corta, no un 500."""
    texto = generar_borrador_takedown(dominio_clon="a.test", dominio_legitimo="b.test")
    assert "a.test" in texto and "b.test" in texto
    assert "sin registros A publicados" in texto


def test_vcard_a_dict_aplana_jcard_de_rdap():
    vcard = [
        "vcard",
        [
            ["version", {}, "text", "4.0"],
            ["fn", {}, "text", "Equipo de Abuso"],
            ["email", {}, "text", "abuse@x.test"],
            ["email", {}, "text", "segundo@x.test"],
        ],
    ]
    assert _vcard_a_dict(vcard) == {
        "version": "4.0",
        "fn": "Equipo de Abuso",
        "email": "abuse@x.test",
    }
    assert _vcard_a_dict(None) == {}
    assert _vcard_a_dict(["vcard"]) == {}


# ---------------------------------------------------------------------------
# Sección "Exposición de marca" del export ejecutivo
# ---------------------------------------------------------------------------


def _markdown(dominios_clon=None, kpis_marca=None) -> str:
    return construir_markdown_ejecutivo(
        tenant_nombre="Cliente Real S.A.S.",
        assets=[],
        postura_por_activo={},
        kpis={"porcentaje_dentro_sla": None, "mttr_por_severidad": []},
        generado_en="2026-07-31T00:00:00+00:00",
        dominios_clon=dominios_clon,
        kpis_marca=kpis_marca,
    )


def test_export_ejecutivo_declara_la_seccion_de_marca_aunque_este_vacia():
    md = _markdown()
    assert "## Exposición de marca" in md
    assert "No hay vigilancia de marca ejecutada" in md


def test_export_ejecutivo_incluye_dominio_score_y_takedown():
    md = _markdown(
        dominios_clon=[
            {
                "endpoint": "mibanko.test",
                "severidad": "critical",
                "estado": "descubierto",
                "score": 3,
                "scoring": {"dns_a": True, "mx": True, "ssl": True, "http": False},
                "screenshot": "/antisuplantacion/dominios/x/screenshot",
                "takedown": "Para: abuse@registrador.test\nAsunto: Solicitud de suspensión",
                "primera_vez_visto": "2026-07-20T00:00:00+00:00",
            }
        ],
        kpis_marca={"total_clones": 1, "clones_activos": 1, "mttd_dias": 4.0, "tttd_dias": None},
    )
    assert "mibanko.test" in md
    assert "3/4" in md
    assert "abuse@registrador.test" in md
    assert "Riesgo de correo" in md
    assert "captura de pantalla disponible" in md
    assert "**Suplantaciones detectadas:** 1" in md


def test_export_ejecutivo_ordena_por_score_descendente():
    md = _markdown(
        dominios_clon=[
            {"endpoint": "bajo.test", "severidad": "high", "estado": "descubierto", "score": 0, "scoring": {}},
            {"endpoint": "alto.test", "severidad": "critical", "estado": "descubierto", "score": 4, "scoring": {}},
        ]
    )
    assert md.index("alto.test") < md.index("bajo.test")


def test_export_ejecutivo_sin_screenshot_lo_dice_sin_parecer_un_error():
    md = _markdown(
        dominios_clon=[
            {
                "endpoint": "sincaptura.test",
                "severidad": "high",
                "estado": "descubierto",
                "score": 1,
                "scoring": {"dns_a": True},
                "screenshot": None,
            }
        ]
    )
    assert "sin captura" in md.lower()
    assert "error" not in md.lower()


# ---------------------------------------------------------------------------
# Integración: el endpoint de descarga incluye el hallazgo de marca
# ---------------------------------------------------------------------------


def test_reporte_ejecutivo_descargable_incluye_hallazgo_de_marca(client):
    resp = client.post(
        "/auth/register",
        json={
            "nombre_negocio": "Cliente Con Marca",
            "email": "marca@ejecutivo.test",
            "password": "claveSegura123",
        },
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    tenant_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["tenant_id"]

    conn = get_conn()
    try:
        asset_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO assets (id, tenant_id, tipo, valor, verificado, verification_token) "
            "VALUES (?, ?, 'dominio', 'mibanco.test', 1, 'tok')",
            (asset_id, tenant_id),
        )
        scan_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO scans (id, tenant_id, asset_id, target, autorizacion_firmada, estado, trace_log_json) "
            "VALUES (?, ?, ?, 'mibanco.test', 1, 'completado', '[]')",
            (scan_id, tenant_id, asset_id),
        )
        conn.execute(
            "INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json, estado) "
            "VALUES (?, ?, ?, 'dominio_variante', 'critical', 'mibanko.test', 0, ?, 'descubierto')",
            (
                str(uuid.uuid4()),
                scan_id,
                tenant_id,
                json.dumps(
                    {
                        "señal": {**SEÑAL_BASE, "takedown": "BORRADOR-REAL-DE-TAKEDOWN"},
                        "target": "mibanco.test",
                        "analisis_claude": None,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # El panel ve el borrador determinista aunque `analisis_claude` sea None.
    dominios = client.get(
        "/antisuplantacion/dominios", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert len(dominios) == 1
    assert dominios[0]["takedown"] == "BORRADOR-REAL-DE-TAKEDOWN"
    assert dominios[0]["tipo"] == "dominio_variante"
    assert dominios[0]["score"] == 3

    resp_pdf = client.get(
        "/reports/ejecutivo/download?formato=pdf", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp_pdf.status_code == 200
    assert len(resp_pdf.content) > 0


def test_tenant_recien_registrado_arranca_en_cero(client):
    """Un cliente nuevo no ve NINGÚN dato precargado.

    Los scripts de siembra (`scripts/seed_demo.py`,
    `scripts/seed_demo_antisuplantacion.py`) solo se ejecutan a mano; nada en
    el arranque de la app ni en las migraciones los invoca. Este test fija esa
    propiedad desde afuera: si alguien alguna vez conecta una siembra al
    arranque o al registro, esto se pone rojo.
    """
    resp = client.post(
        "/auth/register",
        json={
            "nombre_negocio": "Cliente Nuevo Real",
            "email": "nuevo@cliente.test",
            "password": "claveSegura123",
        },
    )
    assert resp.status_code == 200
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    assert client.get("/assets", headers=headers).json() == []
    assert client.get("/findings", headers=headers).json() == []
    assert client.get("/antisuplantacion/dominios", headers=headers).json() == []
    assert client.get("/scans", headers=headers).json() == []
    assert client.get("/watched-brands", headers=headers).json() == []

    kpis_marca = client.get("/antisuplantacion/kpis", headers=headers).json()
    assert kpis_marca["total_clones"] == 0
    assert kpis_marca["clones_activos"] == 0
