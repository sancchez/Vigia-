"""Fase 3a (openspec/changes/fase3a-antisuplantacion-y-scoring):
`POST /antisuplantacion`, `GET /antisuplantacion/dominios`,
`GET /antisuplantacion/dominios/{id}/screenshot`, `GET /antisuplantacion/kpis`.

El nodo (`agents.antisuplantacion.node`) y los probes de infraestructura
(`puntuar_infra_lookalike`/`capturar_screenshot`) se mockean en
`api.main` -- sin dnstwist/Sherlock/red real ni Playwright real en la
suite, mismo principio que el resto del proyecto (ver
`test_scan_activo_asset_gate.py`).
"""

from __future__ import annotations

import json
import uuid

import api.main as main_module
from db.connection import get_conn
from tools.antisuplantacion import InfraScore


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123"):
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _tenant_id_de(client, token: str) -> str:
    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    return resp.json()["tenant_id"]


def _crear_asset(client, token: str, tipo: str, valor: str) -> str:
    resp = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token}"},
        json={"tipo": tipo, "valor": valor},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _verificar_asset_mock(client, token: str, asset_id: str, monkeypatch) -> None:
    """Marca un asset como verificado sin hacer DNS/HTTP real -- mismo patrón
    que `test_scan_activo_asset_gate.py::test_scan_activo_permite_target_con_asset_registrado`."""
    monkeypatch.setattr(main_module, "verificar_asset", lambda *_a, **_k: (True, "mock: token encontrado"))
    resp = client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token}"},
        json={"metodo": "dns_txt"},
    )
    assert resp.status_code == 200
    assert resp.json()["verificado"] is True


def _mockear_nodo_y_probes(monkeypatch, *, con_señales: bool = True):
    """Mockea `agents.antisuplantacion.node` (importado en `api.main` como
    `antisuplantacion_node`) y los probes de infraestructura, para que
    `POST /antisuplantacion` corra determinista y sin red real."""
    if con_señales:
        señales = [
            {
                "señales_crudas": [
                    {
                        "tipo": "dominio_variante",
                        "fuzzer": "bitsquatting",
                        "dominio": "clon-malicioso.test",
                        "dns_a": ["1.2.3.4"],
                        "registrado": True,
                    }
                ],
                "analisis_claude": "Borrador de takedown de prueba: probable suplantación.",
            }
        ]
    else:
        señales = []

    monkeypatch.setattr(
        main_module,
        "antisuplantacion_node",
        lambda _estado: {"antisuplantacion_findings": señales},
    )
    monkeypatch.setattr(
        main_module,
        "puntuar_infra_lookalike",
        lambda _dominio, timeout=10: InfraScore(
            dns_a=True, mx=False, ssl=True, http=True, ssl_not_before="2026-01-01T00:00:00+00:00", score=3
        ),
    )
    monkeypatch.setattr(main_module, "capturar_screenshot", lambda _url, _out_dir, timeout=20: None)


# ---------------------------------------------------------------------------
# POST /antisuplantacion
# ---------------------------------------------------------------------------


def test_post_antisuplantacion_rechaza_asset_no_verificado(client, monkeypatch):
    _mockear_nodo_y_probes(monkeypatch)
    token = _registrar_tenant(client, "Pyme Sin Verificar", "sinverificar@antisuplantacion.test")
    asset_id = _crear_asset(client, token, "dominio", "dominiopublico.test")

    resp = client.post(
        "/antisuplantacion",
        headers={"Authorization": f"Bearer {token}"},
        json={"asset_id": asset_id},
    )

    assert resp.status_code == 403
    assert conteo_findings_de(_tenant_id_de(client, token)) == 0


def conteo_findings_de(tenant_id: str) -> int:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM findings WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()["c"]
    finally:
        conn.close()


def test_post_antisuplantacion_rechaza_asset_de_otro_tenant(client, monkeypatch):
    _mockear_nodo_y_probes(monkeypatch)
    token_dueno = _registrar_tenant(client, "Dueno Real", "dueno@antisuplantacion.test")
    asset_id = _crear_asset(client, token_dueno, "dominio", "midominio-legit.test")
    _verificar_asset_mock(client, token_dueno, asset_id, monkeypatch)

    token_atacante = _registrar_tenant(client, "Otro Tenant", "atacante@antisuplantacion.test")
    resp = client.post(
        "/antisuplantacion",
        headers={"Authorization": f"Bearer {token_atacante}"},
        json={"asset_id": asset_id},
    )

    assert resp.status_code == 404


def test_post_antisuplantacion_crea_findings_con_score_en_raw_json(client, monkeypatch):
    _mockear_nodo_y_probes(monkeypatch)
    token = _registrar_tenant(client, "Pyme Protegida", "protegida@antisuplantacion.test")
    asset_id = _crear_asset(client, token, "dominio", "midominio-protegido.test")
    _verificar_asset_mock(client, token, asset_id, monkeypatch)

    resp = client.post(
        "/antisuplantacion",
        headers={"Authorization": f"Bearer {token}"},
        json={"asset_id": asset_id},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["scan_id"]
    assert len(data["dominios"]) == 1
    clon = data["dominios"][0]
    assert clon["endpoint"] == "clon-malicioso.test"
    assert clon["score"] == 3
    assert clon["scoring"] == {"dns_a": True, "mx": False, "ssl": True, "http": True}
    assert clon["takedown"] == "Borrador de takedown de prueba: probable suplantación."
    assert clon["screenshot"] is None

    # Confirma que el score/scoring de verdad viajó a `findings.raw_json`
    # (spec.md: "El desglose booleano por señal MUST guardarse en
    # findings.raw_json"), no solo en la respuesta HTTP.
    tenant_id = _tenant_id_de(client, token)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT raw_json FROM findings WHERE tenant_id = ? AND tipo = 'dominio_variante'",
            (tenant_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    raw = json.loads(row["raw_json"])
    assert raw["señal"]["score"] == 3
    assert raw["señal"]["scoring"]["dns_a"] is True


def test_post_antisuplantacion_rechaza_asset_tipo_no_dominio(client, monkeypatch):
    _mockear_nodo_y_probes(monkeypatch)
    token = _registrar_tenant(client, "Pyme IP", "ip@antisuplantacion.test")
    asset_id = _crear_asset(client, token, "ip", "203.0.113.9")

    resp = client.post(
        "/antisuplantacion",
        headers={"Authorization": f"Bearer {token}"},
        json={"asset_id": asset_id},
    )

    assert resp.status_code == 422


def test_post_antisuplantacion_asset_inexistente_da_404(client, monkeypatch):
    _mockear_nodo_y_probes(monkeypatch)
    token = _registrar_tenant(client, "Pyme X", "x@antisuplantacion.test")

    resp = client.post(
        "/antisuplantacion",
        headers={"Authorization": f"Bearer {token}"},
        json={"asset_id": str(uuid.uuid4())},
    )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /antisuplantacion/dominios -- tenant scoping
# ---------------------------------------------------------------------------


def _sembrar_finding_clon(
    tenant_id: str,
    *,
    endpoint: str = "clon.test",
    score: int = 3,
    ssl_not_before: str | None = None,
    primera_vez_visto: str | None = None,
    estado: str = "descubierto",
) -> str:
    conn = get_conn()
    try:
        scan_id = str(uuid.uuid4())
        finding_id = str(uuid.uuid4())
        raw_json = {
            "señal": {
                "tipo": "dominio_variante",
                "dominio": endpoint,
                "score": score,
                "scoring": {"dns_a": True, "mx": False, "ssl": score >= 3, "http": score >= 2},
                "ssl_not_before": ssl_not_before,
                "screenshot": None,
            },
            "target": "miempresa.test",
            "analisis_claude": "Borrador de takedown.",
            "fuente": "antisuplantacion_bajo_demanda",
        }
        conn.execute(
            "INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado, trace_log_json) "
            "VALUES (?, ?, ?, 1, 'completado', '[]')",
            (scan_id, tenant_id, "miempresa.test"),
        )
        conn.execute(
            """
            INSERT INTO findings
                (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json,
                 estado, primera_vez_visto, ultima_vez_visto)
            VALUES (?, ?, ?, 'dominio_variante', 'high', ?, 0, ?, ?, ?, ?)
            """,
            (
                finding_id,
                scan_id,
                tenant_id,
                endpoint,
                json.dumps(raw_json),
                estado,
                primera_vez_visto,
                primera_vez_visto,
            ),
        )
        conn.commit()
        return finding_id
    finally:
        conn.close()


def test_get_dominios_clon_scoped_por_tenant(client):
    token_a = _registrar_tenant(client, "Tenant A", "a@dominios.test")
    token_b = _registrar_tenant(client, "Tenant B", "b@dominios.test")
    tenant_a = _tenant_id_de(client, token_a)
    tenant_b = _tenant_id_de(client, token_b)

    _sembrar_finding_clon(tenant_a, endpoint="clon-a1.test")
    _sembrar_finding_clon(tenant_a, endpoint="clon-a2.test")
    _sembrar_finding_clon(tenant_b, endpoint="clon-b1.test")

    resp_a = client.get("/antisuplantacion/dominios", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_a.status_code == 200
    endpoints_a = {d["endpoint"] for d in resp_a.json()}
    assert endpoints_a == {"clon-a1.test", "clon-a2.test"}

    resp_b = client.get("/antisuplantacion/dominios", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.status_code == 200
    assert {d["endpoint"] for d in resp_b.json()} == {"clon-b1.test"}


def test_get_dominios_clon_vacio_sin_clones(client):
    token = _registrar_tenant(client, "Tenant Sin Clones", "sinclones@dominios.test")
    resp = client.get("/antisuplantacion/dominios", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /antisuplantacion/dominios/{id}/screenshot
# ---------------------------------------------------------------------------


def test_screenshot_de_otro_tenant_da_404(client, tmp_path):
    token_dueno = _registrar_tenant(client, "Dueno Screenshot", "dueno@screenshot.test")
    tenant_dueno = _tenant_id_de(client, token_dueno)

    archivo_screenshot = tmp_path / "clon.png"
    archivo_screenshot.write_bytes(b"fake-png-bytes")

    conn = get_conn()
    try:
        scan_id = str(uuid.uuid4())
        finding_id = str(uuid.uuid4())
        raw_json = {"señal": {"tipo": "dominio_variante", "dominio": "clon.test", "screenshot": str(archivo_screenshot)}}
        conn.execute(
            "INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado, trace_log_json) "
            "VALUES (?, ?, 'miempresa.test', 1, 'completado', '[]')",
            (scan_id, tenant_dueno),
        )
        conn.execute(
            "INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json) "
            "VALUES (?, ?, ?, 'dominio_variante', 'high', 'clon.test', 0, ?)",
            (finding_id, scan_id, tenant_dueno, json.dumps(raw_json)),
        )
        conn.commit()
    finally:
        conn.close()

    token_atacante = _registrar_tenant(client, "Atacante Screenshot", "atacante@screenshot.test")
    resp_ajeno = client.get(
        f"/antisuplantacion/dominios/{finding_id}/screenshot",
        headers={"Authorization": f"Bearer {token_atacante}"},
    )
    assert resp_ajeno.status_code == 404

    resp_dueno = client.get(
        f"/antisuplantacion/dominios/{finding_id}/screenshot",
        headers={"Authorization": f"Bearer {token_dueno}"},
    )
    assert resp_dueno.status_code == 200
    assert resp_dueno.content == b"fake-png-bytes"


def test_screenshot_404_si_finding_inexistente(client):
    token = _registrar_tenant(client, "Tenant Sin Finding", "sinfinding@screenshot.test")
    resp = client.get(
        f"/antisuplantacion/dominios/{uuid.uuid4()}/screenshot",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /antisuplantacion/kpis
# ---------------------------------------------------------------------------


def test_kpis_vacio_sin_dominios_clon(client):
    token = _registrar_tenant(client, "Tenant Sin KPIs", "sinkpis@kpis.test")
    resp = client.get("/antisuplantacion/kpis", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["tttd_dias"] is None
    assert data["mttd_dias"] is None
    assert data["total_clones"] == 0
    assert data["clones_activos"] == 0


def test_kpis_tttd_exacto_con_un_clon_cerrado(client):
    token = _registrar_tenant(client, "Tenant TTTD", "tttd@kpis.test")
    tenant_id = _tenant_id_de(client, token)

    primera_vez_visto = "2026-01-01T00:00:00+00:00"
    finding_id = _sembrar_finding_clon(
        tenant_id, endpoint="clon-cerrado.test", primera_vez_visto=primera_vez_visto, estado="cerrado"
    )

    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO finding_transiciones
                (id, finding_id, tenant_id, estado_anterior, estado_nuevo, created_at)
            VALUES (?, ?, ?, 'verificado', 'cerrado', ?)
            """,
            (str(uuid.uuid4()), finding_id, tenant_id, "2026-01-06T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/antisuplantacion/kpis", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["tttd_dias"] == 5.0
    assert data["tttd_precision"] == "exacto"
    assert data["total_clones"] == 1


def test_kpis_mttd_null_sin_evidencia_ssl(client):
    token = _registrar_tenant(client, "Tenant Sin SSL", "sinssl@kpis.test")
    tenant_id = _tenant_id_de(client, token)
    _sembrar_finding_clon(
        tenant_id,
        endpoint="clon-sin-ssl.test",
        ssl_not_before=None,
        primera_vez_visto="2026-01-01T00:00:00+00:00",
    )

    resp = client.get("/antisuplantacion/kpis", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["mttd_dias"] is None


def test_kpis_mttd_calculado_cuando_hay_ssl_not_before(client):
    token = _registrar_tenant(client, "Tenant Con SSL", "consssl@kpis.test")
    tenant_id = _tenant_id_de(client, token)
    _sembrar_finding_clon(
        tenant_id,
        endpoint="clon-con-ssl.test",
        ssl_not_before="2025-12-28T00:00:00+00:00",
        primera_vez_visto="2026-01-01T00:00:00+00:00",
    )

    resp = client.get("/antisuplantacion/kpis", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["mttd_dias"] == 4.0
    assert data["mttd_precision"] == "aproximado"


# ---------------------------------------------------------------------------
# Fase 9: cableado del filtro de defensivos en el camino de persistencia.
# Regresión MEDIDA (2026-07-25): `triar_lookalikes` existía y tenía tests,
# pero NINGÚN camino de producción lo usaba -- el builder persistía los
# registros defensivos del propio cliente como amenazas "high". Estos tests
# protegen el cableado real (`_extraer_findings_antisuplantacion`), no la
# función pura.
# ---------------------------------------------------------------------------

def _senal_dominio(dominio, *, defensivo=False, correo=False, registrado=True):
    return {
        "señales_crudas": [
            {
                "tipo": "dominio_variante",
                "fuzzer": "addition",
                "dominio": dominio,
                "registrado": registrado,
                "es_defensivo": defensivo,
                "tiene_correo": correo,
            }
        ],
        "analisis_claude": None,
    }


def test_persistencia_excluye_registros_defensivos_del_cliente():
    """Un lookalike marcado `es_defensivo` (mismo nameserver que el dominio
    legítimo) NO debe generar una fila: es la defensa del cliente, no una
    amenaza. Sin este filtro se le reportaría su propia infraestructura."""
    filas = main_module._extraer_findings_antisuplantacion(
        "marca.com",
        [_senal_dominio("marca-defensiva.com", defensivo=True)],
    )
    assert filas == []


def test_persistencia_eleva_a_critical_los_que_pueden_enviar_correo():
    """Tercero con MX real -> puede enviar phishing que parezca del cliente ->
    severidad critical (encabeza el informe). Tercero sin correo -> high."""
    filas = main_module._extraer_findings_antisuplantacion(
        "marca.com",
        [
            _senal_dominio("marca-con-correo.com", correo=True),
            _senal_dominio("marca-sin-correo.com", correo=False),
        ],
    )
    por_endpoint = {f["endpoint"]: f["severidad"] for f in filas}
    assert por_endpoint == {
        "marca-con-correo.com": "critical",
        "marca-sin-correo.com": "high",
    }


# ---------------------------------------------------------------------------
# Fase 10 WS3: triaje honesto de perfiles de redes sociales (Sherlock).
# MEDIDO (2026-07-26): `sherlock nubank --print-found` sobre 68 plataformas
# reales solo devuelve URL + existencia -- sin bio, sin enlace al dominio del
# cliente, sin ninguna señal para distinguir el perfil oficial de una
# entidad no relacionada o de un suplantador real. Sin esa señal, clasificar
# automáticamente sería inventar un clasificador que miente. Por eso TODO
# hallazgo `perfil_red_social` se persiste como severidad `info` ("revisión
# humana"), nunca como amenaza medium/high/critical. Este test protege esa
# decisión: si alguien revierte la degradación a `info` (ej. la vuelve a
# `medium` para "que se vea más urgente"), este test falla.
# ---------------------------------------------------------------------------

def _senal_perfil(url: str) -> dict:
    return {
        "señales_crudas": [
            {"tipo": "perfil_red_social", "username": "marca", "url": url},
        ],
        "analisis_claude": None,
    }


def test_persistencia_degrada_perfiles_red_social_a_info_para_revision_humana():
    """Ningún perfil de red social se persiste como amenaza sin evidencia:
    Sherlock no da señal para distinguir oficial/no-relacionado/suplantador
    (ver comentario en `_extraer_findings_antisuplantacion` y en
    `tools.antisuplantacion.run_sherlock`), así que todos quedan en `info`."""
    filas = main_module._extraer_findings_antisuplantacion(
        "marca.com",
        [
            _senal_perfil("https://twitter.com/marca"),
            _senal_perfil("https://github.com/marca"),
        ],
    )
    assert len(filas) == 2
    assert {f["severidad"] for f in filas} == {"info"}
    # Ninguna severidad de amenaza confirmada, ni una sola vez.
    assert not any(f["severidad"] in {"medium", "high", "critical"} for f in filas)


# ---------------------------------------------------------------------------
# Fase 10 WS1: la deduplicación NO debe fusionar dominios suplantadores
# distintos. BUG MEDIDO (2026-07-26): `urlparse()` sobre un dominio pelado
# devuelve `netloc` vacío, así que los 35 lookalikes de una corrida real
# cayeron todos en la clave `("dominio_variante", "")` y se persistió UN solo
# hallazgo -- el cliente habría visto "1 amenaza" en vez de 35.
# ---------------------------------------------------------------------------

def test_dedup_no_fusiona_dominios_suplantadores_distintos():
    """Cada dominio típosquat es una amenaza distinta, no la misma repetida.
    Si alguien revierte el fallback a `endpoint` en
    `_clave_agrupacion_hallazgo`, estos tres colapsan en uno y el test falla."""
    hallazgos = [
        {"tipo": "dominio_variante", "endpoint": d, "raw_json": None}
        for d in ("bancolombias.com", "bamcolombia.com", "bancolornbia.com")
    ]
    claves = {main_module._clave_agrupacion_hallazgo(h) for h in hallazgos}
    assert len(claves) == 3, f"se fusionaron dominios distintos: {claves}"


def test_dedup_sigue_agrupando_alertas_zap_del_mismo_sitio():
    """El fix de arriba no debe romper el comportamiento de Fase 8: la misma
    alerta ZAP sobre muchas URLs del MISMO sitio sigue siendo un solo grupo
    (esos endpoints sí traen esquema, así que usan la rama `netloc`)."""
    hallazgos = [
        {"tipo": "CSP Header Not Set", "endpoint": f"http://localhost:3000/pagina{i}", "raw_json": None}
        for i in range(5)
    ]
    claves = {main_module._clave_agrupacion_hallazgo(h) for h in hallazgos}
    assert len(claves) == 1, f"dejó de agrupar alertas del mismo sitio: {claves}"
