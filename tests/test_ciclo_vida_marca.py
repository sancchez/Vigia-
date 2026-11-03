"""Ciclo de vida del hallazgo APLICADO A MARCA + barrido programado de marca.

Cubre lo que se agregó al cerrar antisuplantación:

* `POST /antisuplantacion/dominios/{id}/takedown-enviado` -- avanza el
  hallazgo hasta `en_progreso` recorriendo la máquina de estados paso a paso
  y guarda la evidencia de lo enviado.
* `POST /antisuplantacion/dominios/{id}/sin-respuesta` -- registra el silencio
  del registrador SIN cambiar de estado (es un evento repetible, no un
  estado), auditado en `finding_transiciones`.
* `api.main::_etapa_marca` -- traducción del estado genérico al vocabulario
  de marca.
* `api.scheduler::run_brand_cycle_once` -- el barrido programado que hace que
  "vigilancia continua" sea cierta sin depender del feed de CertStream.

Mismo principio que el resto de la suite: nada de red real (dnstwist,
Sherlock, RDAP y los probes de infraestructura se mockean).
"""

from __future__ import annotations

import json

import api.main as main_module
import api.scheduler as scheduler_module
from db.connection import get_conn
from tools.antisuplantacion import InfraScore

from tests.test_antisuplantacion_endpoint import (
    _crear_asset,
    _mockear_nodo_y_probes,
    _registrar_tenant,
    _tenant_id_de,
    _verificar_asset_mock,
    correr_antisuplantacion,
)


def _hallazgo_de_marca(client, monkeypatch) -> tuple[str, str, str]:
    """Deja un tenant con un dominio verificado y UN hallazgo `dominio_variante`.

    Devuelve `(token, asset_id, finding_id)`.
    """
    _mockear_nodo_y_probes(monkeypatch)
    token = _registrar_tenant(client, "Pyme Con Clon", "clon@ciclomarca.test")
    asset_id = _crear_asset(client, token, "dominio", "milegitimo.test")
    _verificar_asset_mock(client, token, asset_id, monkeypatch)

    # El endpoint es asíncrono (202 + polling) desde 2026-08-02 -- ver
    # `correr_antisuplantacion` en test_antisuplantacion_endpoint.py.
    corrida = correr_antisuplantacion(client, token, asset_id)
    assert corrida["estado"] == "completado", corrida
    dominios = corrida["dominios"]
    assert len(dominios) == 1, dominios
    return token, asset_id, dominios[0]["finding_id"]


def _transiciones(finding_id: str) -> list[dict]:
    conn = get_conn()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT estado_anterior, estado_nuevo, nota FROM finding_transiciones "
                "WHERE finding_id = ? ORDER BY created_at, rowid",
                (finding_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


def _raw_json(finding_id: str) -> dict:
    conn = get_conn()
    try:
        row = conn.execute("SELECT raw_json FROM findings WHERE id = ?", (finding_id,)).fetchone()
    finally:
        conn.close()
    return json.loads(row["raw_json"] or "{}")


# ---------------------------------------------------------------------------
# _etapa_marca (puro)
# ---------------------------------------------------------------------------


def test_etapa_marca_traduce_estados_al_vocabulario_del_cliente():
    assert main_module._etapa_marca("descubierto", None, 0) == "Detectado"
    assert main_module._etapa_marca("triage", None, 0) == "En triage"
    # `en_progreso` SIN evidencia de envío no puede decir "Takedown enviado":
    # sería exactamente el falso verde que este módulo existe para evitar.
    assert main_module._etapa_marca("en_progreso", None, 0) == "En gestión"
    assert main_module._etapa_marca("en_progreso", "2026-08-01T00:00:00", 0) == "Takedown enviado"
    assert "sin respuesta (2)" in main_module._etapa_marca("en_progreso", "2026-08-01T00:00:00", 2)
    assert main_module._etapa_marca("cerrado", "x", 0).startswith("Resuelto")
    assert main_module._etapa_marca("aceptado_riesgo", None, 0).startswith("Desestimado")


# ---------------------------------------------------------------------------
# POST /antisuplantacion/dominios/{id}/takedown-enviado
# ---------------------------------------------------------------------------


def test_takedown_enviado_avanza_hop_a_hop_y_guarda_evidencia(client, monkeypatch):
    token, _asset_id, finding_id = _hallazgo_de_marca(client, monkeypatch)

    resp = client.post(
        f"/antisuplantacion/dominios/{finding_id}/takedown-enviado",
        headers={"Authorization": f"Bearer {token}"},
        json={"destinatario": "abuse@registrador.test", "texto_enviado": "carta enviada"},
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["estado"] == "en_progreso"
    assert cuerpo["etapa_marca"] == "Takedown enviado"
    assert cuerpo["takedown_destinatario"] == "abuse@registrador.test"
    assert cuerpo["takedown_enviado_en"]

    # El historial tiene los TRES saltos reales, no un salto sintético: es lo
    # que hace que el MTTR/TTTD se calcule sobre transiciones auditables.
    saltos = [(t["estado_anterior"], t["estado_nuevo"]) for t in _transiciones(finding_id)]
    assert saltos == [
        ("descubierto", "triage"),
        ("triage", "asignado"),
        ("asignado", "en_progreso"),
    ]

    raw = _raw_json(finding_id)
    assert raw["takedown_texto_enviado"] == "carta enviada"
    # La evidencia previa (la señal cruda) no se pisa al agregar la del envío.
    assert "señal" in raw


def test_takedown_enviado_es_idempotente_hacia_adelante(client, monkeypatch):
    """Reenviar la carta tras el silencio del registrador es el caso NORMAL --
    no debe duplicar saltos ni retroceder el estado."""
    token, _asset_id, finding_id = _hallazgo_de_marca(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    client.post(f"/antisuplantacion/dominios/{finding_id}/takedown-enviado", headers=headers, json={})
    saltos_tras_primero = len(_transiciones(finding_id))

    resp = client.post(
        f"/antisuplantacion/dominios/{finding_id}/takedown-enviado",
        headers=headers,
        json={"destinatario": "otro@registrador.test"},
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "en_progreso"
    assert len(_transiciones(finding_id)) == saltos_tras_primero
    # La evidencia SÍ se actualiza al último envío.
    assert resp.json()["takedown_destinatario"] == "otro@registrador.test"


def test_takedown_enviado_rechaza_hallazgo_terminal(client, monkeypatch):
    token, _asset_id, finding_id = _hallazgo_de_marca(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.patch(
        f"/findings/{finding_id}",
        headers=headers,
        json={"estado": "aceptado_riesgo", "nota": "es un dominio de un socio"},
    )
    assert resp.status_code == 200

    resp = client.post(
        f"/antisuplantacion/dominios/{finding_id}/takedown-enviado", headers=headers, json={}
    )
    assert resp.status_code == 409


def test_takedown_enviado_404_cross_tenant(client, monkeypatch):
    token, _asset_id, finding_id = _hallazgo_de_marca(client, monkeypatch)
    otro = _registrar_tenant(client, "Ajeno", "ajeno@ciclomarca.test")
    resp = client.post(
        f"/antisuplantacion/dominios/{finding_id}/takedown-enviado",
        headers={"Authorization": f"Bearer {otro}"},
        json={},
    )
    assert resp.status_code == 404
    # Y el hallazgo del dueño no se tocó.
    assert _transiciones(finding_id) == []


def test_takedown_enviado_rechaza_hallazgo_que_no_es_de_marca(client, monkeypatch):
    """Un finding técnico (ej. una vuln web) no se "manda a un registrador"."""
    token = _registrar_tenant(client, "Pyme Tecnica", "tecnica@ciclomarca.test")
    tenant_id = _tenant_id_de(client, token)
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado) "
            "VALUES ('s-tec', ?, 'x.test', 0, 'completado')",
            (tenant_id,),
        )
        conn.execute(
            "INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json) "
            "VALUES ('f-tec', 's-tec', ?, 'sqli', 'high', 'https://x.test/a', 1, '{}')",
            (tenant_id,),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.post(
        "/antisuplantacion/dominios/f-tec/takedown-enviado",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /antisuplantacion/dominios/{id}/sin-respuesta
# ---------------------------------------------------------------------------


def test_sin_respuesta_exige_takedown_previo(client, monkeypatch):
    """"No respondió" no significa nada si nadie preguntó."""
    token, _asset_id, finding_id = _hallazgo_de_marca(client, monkeypatch)
    resp = client.post(
        f"/antisuplantacion/dominios/{finding_id}/sin-respuesta",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert resp.status_code == 409


def test_sin_respuesta_acumula_sin_cambiar_estado(client, monkeypatch):
    token, _asset_id, finding_id = _hallazgo_de_marca(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}
    client.post(f"/antisuplantacion/dominios/{finding_id}/takedown-enviado", headers=headers, json={})

    for esperado in (1, 2):
        resp = client.post(
            f"/antisuplantacion/dominios/{finding_id}/sin-respuesta",
            headers=headers,
            json={"dias_esperados": 15},
        )
        assert resp.status_code == 200
        assert resp.json()["registrador_sin_respuesta"] == esperado
        # El estado NO cambia: esperar la 2a respuesta no es un estado distinto
        # de esperar la 1a.
        assert resp.json()["estado"] == "en_progreso"

    assert "sin respuesta (2)" in resp.json()["etapa_marca"]

    # Las dos quedan auditadas, sin ensuciar la máquina de estados.
    sin_estado = [t for t in _transiciones(finding_id) if t["estado_nuevo"] is None]
    assert len(sin_estado) == 2
    assert "15 día(s)" in sin_estado[0]["nota"]


def test_ciclo_completo_hasta_resuelto(client, monkeypatch):
    """detectado -> takedown enviado -> sin respuesta -> mitigado -> verificado
    -> cerrado, que es la pregunta que hace un cliente ("¿y después qué?")."""
    token, _asset_id, finding_id = _hallazgo_de_marca(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    client.post(f"/antisuplantacion/dominios/{finding_id}/takedown-enviado", headers=headers, json={})
    client.post(f"/antisuplantacion/dominios/{finding_id}/sin-respuesta", headers=headers, json={})

    for estado in ("mitigado", "verificado", "cerrado"):
        resp = client.patch(
            f"/findings/{finding_id}",
            headers=headers,
            json={"estado": estado, "nota": f"paso a {estado}"},
        )
        assert resp.status_code == 200, resp.text

    resp = client.get("/antisuplantacion/dominios", headers=headers)
    fila = next(d for d in resp.json() if d["finding_id"] == finding_id)
    assert fila["estado"] == "cerrado"
    assert fila["etapa_marca"].startswith("Resuelto")

    # TTTD sale de las transiciones reales, no de un campo inventado.
    kpis = client.get("/antisuplantacion/kpis", headers=headers).json()
    assert kpis["tttd_dias"] is not None


# ---------------------------------------------------------------------------
# api/scheduler.py::run_brand_cycle_once
# ---------------------------------------------------------------------------


def _mockear_barrido(monkeypatch, dominio_clon: str = "clon-programado.test"):
    # OJO: `run_brand_cycle_once` hace `from agents.antisuplantacion import node`
    # DENTRO de la función (import diferido para evitar el ciclo con api.main),
    # así que hay que parchear el módulo de origen -- parchear
    # `api.main.antisuplantacion_node` no lo intercepta y el test terminaría
    # ejecutando dnstwist real contra la red (que fue exactamente lo que pasó
    # la primera vez que se escribió este test).
    import agents.antisuplantacion as agente_antisup

    monkeypatch.setattr(
        agente_antisup,
        "node",
        lambda _estado: {
            "antisuplantacion_findings": [
                {
                    "señales_crudas": [
                        {
                            "tipo": "dominio_variante",
                            "fuzzer": "omission",
                            "dominio": dominio_clon,
                            "dns_a": ["9.9.9.9"],
                            "registrado": True,
                        }
                    ],
                    "analisis_claude": None,
                }
            ],
            "trace_log": [],
        },
    )
    monkeypatch.setattr(
        main_module,
        "puntuar_infra_lookalike",
        lambda _d, timeout=10: InfraScore(dns_a=True, mx=True, ssl=False, http=False, score=2),
    )
    monkeypatch.setattr(main_module, "capturar_screenshot", lambda *_a, **_k: None)


def test_barrido_de_marca_omite_dominio_no_verificado(client, monkeypatch):
    """El gate legal vale igual para el camino automático: un dominio
    registrado pero no verificado NO se vigila."""
    _mockear_barrido(monkeypatch)
    token = _registrar_tenant(client, "Sin Verificar", "sv@ciclomarca.test")
    _crear_asset(client, token, "dominio", "noverificado.test")

    assert scheduler_module.run_brand_cycle_once() == 0
    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) AS c FROM findings").fetchone()["c"] == 0
    finally:
        conn.close()


def test_barrido_de_marca_escribe_hallazgos_para_dominio_verificado(client, monkeypatch):
    _mockear_barrido(monkeypatch)
    token = _registrar_tenant(client, "Verificada", "ver@ciclomarca.test")
    tenant_id = _tenant_id_de(client, token)
    asset_id = _crear_asset(client, token, "dominio", "verificado.test")
    _verificar_asset_mock(client, token, asset_id, monkeypatch)

    assert scheduler_module.run_brand_cycle_once() == 1

    resp = client.get("/antisuplantacion/dominios", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    endpoints = [d["endpoint"] for d in resp.json()]
    assert "clon-programado.test" in endpoints

    conn = get_conn()
    try:
        scan = conn.execute(
            "SELECT autorizacion_firmada, user_id, reporte_final FROM scans WHERE tenant_id = ? "
            "AND reporte_final LIKE 'Barrido programado%'",
            (tenant_id,),
        ).fetchone()
    finally:
        conn.close()
    assert scan is not None
    # El barrido NO ejecuta escaneo activo y no lo dispara una persona.
    assert scan["autorizacion_firmada"] == 0
    assert scan["user_id"] is None


def test_barrido_de_marca_respeta_su_frecuencia(client, monkeypatch):
    """Correr el ciclo dos veces seguidas no vuelve a sondear infraestructura
    de terceros -- la cortesía de red no es opcional."""
    _mockear_barrido(monkeypatch)
    token = _registrar_tenant(client, "Frecuencia", "freq@ciclomarca.test")
    asset_id = _crear_asset(client, token, "dominio", "frecuencia.test")
    _verificar_asset_mock(client, token, asset_id, monkeypatch)

    assert scheduler_module.run_brand_cycle_once() == 1
    assert scheduler_module.run_brand_cycle_once() == 0


def test_barrido_de_marca_puede_deshabilitarse(client, monkeypatch):
    _mockear_barrido(monkeypatch)
    token = _registrar_tenant(client, "Apagado", "off@ciclomarca.test")
    asset_id = _crear_asset(client, token, "dominio", "apagado.test")
    _verificar_asset_mock(client, token, asset_id, monkeypatch)

    monkeypatch.setenv("VIGIA_BRAND_CYCLE_ENABLED", "false")
    assert scheduler_module.run_brand_cycle_once() == 0


def test_barrido_de_marca_omite_activo_pausado(client, monkeypatch):
    _mockear_barrido(monkeypatch)
    token = _registrar_tenant(client, "Pausado", "pausa@ciclomarca.test")
    asset_id = _crear_asset(client, token, "dominio", "pausado.test")
    _verificar_asset_mock(client, token, asset_id, monkeypatch)

    conn = get_conn()
    try:
        conn.execute("UPDATE assets SET scan_paused = 1 WHERE id = ?", (asset_id,))
        conn.commit()
    finally:
        conn.close()

    assert scheduler_module.run_brand_cycle_once() == 0
