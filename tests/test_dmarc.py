"""Fase 3e (openspec/changes/fase3e-monitoreo-dmarc) -- monitoreo DMARC.

Cubre: parseo de un reporte RUA válido (plano y gzip), rechazo seguro de
XML malicioso (XXE/entidad recursiva) y de una raíz distinta de
`<feedback>`, agregación de remitentes por IP de origen, ingesta vía
`POST /dmarc/reports` (persistencia scoped al tenant, 422 sin persistir
ante un archivo inválido), y aislamiento por tenant de
`GET /dmarc/reports` / `GET /dmarc/summary` -- mismo patrón que
`test_finding_detail.py`.
"""

from __future__ import annotations

import gzip

import pytest

from tools.dmarc import DmarcParseError, aggregate_senders, parse_aggregate_report

# ---------------------------------------------------------------------------
# Fixtures: un XML RUA mínimo válido (2 records) y su variante gzip.
# ---------------------------------------------------------------------------

_RUA_XML = b"""<?xml version="1.0"?>
<feedback>
  <report_metadata>
    <org_name>google.com</org_name>
    <email>noreply-dmarc-support@google.com</email>
    <report_id>123456789</report_id>
    <date_range><begin>1700000000</begin><end>1700086400</end></date_range>
  </report_metadata>
  <policy_published>
    <domain>example.com</domain>
    <adkim>r</adkim><aspf>r</aspf><p>none</p><sp>none</sp><pct>100</pct>
  </policy_published>
  <record>
    <row>
      <source_ip>10.0.0.9</source_ip>
      <count>5</count>
      <policy_evaluated><disposition>none</disposition><dkim>fail</dkim><spf>fail</spf></policy_evaluated>
    </row>
    <identifiers><header_from>example.com</header_from></identifiers>
    <auth_results>
      <dkim><domain>example.com</domain><result>fail</result></dkim>
      <spf><domain>example.com</domain><result>fail</result></spf>
    </auth_results>
  </record>
  <record>
    <row>
      <source_ip>20.1.1.1</source_ip>
      <count>10</count>
      <policy_evaluated><disposition>none</disposition><dkim>pass</dkim><spf>fail</spf></policy_evaluated>
    </row>
    <identifiers><header_from>example.com</header_from></identifiers>
    <auth_results>
      <dkim><domain>example.com</domain><result>pass</result></dkim>
      <spf><domain>example.com</domain><result>fail</result></spf>
    </auth_results>
  </record>
</feedback>
"""

_RUA_XXE = b"""<?xml version="1.0"?>
<!DOCTYPE feedback [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<feedback><report_metadata><org_name>&xxe;</org_name></report_metadata></feedback>
"""

_RUA_BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<feedback><report_metadata><org_name>&lol3;</org_name></report_metadata></feedback>
"""


# ---------------------------------------------------------------------------
# Unit: parser
# ---------------------------------------------------------------------------


def test_parse_xml_valido_devuelve_metadatos_y_records():
    reporte = parse_aggregate_report(_RUA_XML)
    assert reporte["org"] == "google.com"
    assert reporte["dominio"] == "example.com"
    assert reporte["rango_inicio"] is not None
    assert reporte["rango_fin"] is not None
    assert len(reporte["records"]) == 2
    ips = {r["source_ip"] for r in reporte["records"]}
    assert ips == {"10.0.0.9", "20.1.1.1"}


def test_parse_variante_gzip_da_el_mismo_resultado():
    reporte_plano = parse_aggregate_report(_RUA_XML)
    reporte_gzip = parse_aggregate_report(gzip.compress(_RUA_XML))
    assert reporte_plano == reporte_gzip


def test_parse_xxe_lanza_dmarc_parse_error_sin_leer_archivos():
    with pytest.raises(DmarcParseError):
        parse_aggregate_report(_RUA_XXE)


def test_parse_entidad_recursiva_billion_laughs_lanza_dmarc_parse_error():
    with pytest.raises(DmarcParseError):
        parse_aggregate_report(_RUA_BOMB)


def test_parse_sin_feedback_root_lanza_dmarc_parse_error():
    with pytest.raises(DmarcParseError):
        parse_aggregate_report(b"<otra_cosa><x>1</x></otra_cosa>")


def test_parse_archivo_vacio_lanza_dmarc_parse_error():
    with pytest.raises(DmarcParseError):
        parse_aggregate_report(b"")


def test_parse_zip_corrupto_lanza_dmarc_parse_error():
    with pytest.raises(DmarcParseError):
        parse_aggregate_report(b"PK\x03\x04esto no es un zip valido")


# ---------------------------------------------------------------------------
# Unit: agregación
# ---------------------------------------------------------------------------


def test_aggregate_senders_ip_fail_spf_y_dkim_es_no_autorizada():
    reporte = parse_aggregate_report(_RUA_XML)
    agregado = aggregate_senders([reporte])
    por_ip = {a["source_ip"]: a for a in agregado}
    assert por_ip["10.0.0.9"]["autorizado"] is False
    assert por_ip["10.0.0.9"]["mensajes"] == 5


def test_aggregate_senders_ip_con_dkim_pass_alineado_es_autorizada():
    reporte = parse_aggregate_report(_RUA_XML)
    agregado = aggregate_senders([reporte])
    por_ip = {a["source_ip"]: a for a in agregado}
    assert por_ip["20.1.1.1"]["autorizado"] is True
    assert por_ip["20.1.1.1"]["dkim_pass"] == 10


def test_aggregate_senders_ordena_por_mensajes_desc():
    reporte = parse_aggregate_report(_RUA_XML)
    agregado = aggregate_senders([reporte])
    assert agregado[0]["source_ip"] == "20.1.1.1"  # 10 mensajes > 5


def test_aggregate_senders_sin_reportes_devuelve_lista_vacia():
    assert aggregate_senders([]) == []


# ---------------------------------------------------------------------------
# Integración: endpoints (client fixture -- TestClient in-process)
# ---------------------------------------------------------------------------


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123"):
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def test_post_dmarc_reports_ingesta_exitosa_persiste_fila_scoped_al_tenant(client):
    token = _registrar_tenant(client, "Tenant Dmarc A", "dmarca@dmarc.test")
    resp = client.post(
        "/dmarc/reports",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("reporte.xml", _RUA_XML, "application/xml")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["dominio"] == "example.com"
    assert data["n_records"] == 2
    assert "id" in data


def test_post_dmarc_reports_gzip_tambien_ingiere(client):
    token = _registrar_tenant(client, "Tenant Dmarc Gzip", "gzip@dmarc.test")
    resp = client.post(
        "/dmarc/reports",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("reporte.xml.gz", gzip.compress(_RUA_XML), "application/gzip")},
    )
    assert resp.status_code == 200
    assert resp.json()["n_records"] == 2


def test_post_dmarc_reports_archivo_invalido_422_sin_persistir(client):
    token = _registrar_tenant(client, "Tenant Dmarc Invalido", "invalido@dmarc.test")
    resp = client.post(
        "/dmarc/reports",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("reporte.xml", b"esto no es xml", "application/xml")},
    )
    assert resp.status_code == 422

    # No se persistió ninguna fila -- la lista sigue vacía.
    resp_lista = client.get("/dmarc/reports", headers={"Authorization": f"Bearer {token}"})
    assert resp_lista.status_code == 200
    assert resp_lista.json() == []


def test_post_dmarc_reports_xxe_422_sin_persistir(client):
    token = _registrar_tenant(client, "Tenant Dmarc Xxe", "xxe@dmarc.test")
    resp = client.post(
        "/dmarc/reports",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("reporte.xml", _RUA_XXE, "application/xml")},
    )
    assert resp.status_code == 422

    resp_lista = client.get("/dmarc/reports", headers={"Authorization": f"Bearer {token}"})
    assert resp_lista.json() == []


def test_post_dmarc_reports_sin_token_401_o_403(client):
    resp = client.post("/dmarc/reports", files={"file": ("r.xml", _RUA_XML, "application/xml")})
    assert resp.status_code in (401, 403)


def test_get_dmarc_reports_lista_scoped_al_tenant(client):
    token = _registrar_tenant(client, "Tenant Dmarc Lista", "lista@dmarc.test")
    client.post(
        "/dmarc/reports",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("reporte.xml", _RUA_XML, "application/xml")},
    )
    resp = client.get("/dmarc/reports", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["dominio"] == "example.com"
    assert data[0]["n_records"] == 2


def test_get_dmarc_reports_aislamiento_por_tenant(client):
    token_a = _registrar_tenant(client, "Tenant Dmarc Dueno", "dueno@dmarc.test")
    token_b = _registrar_tenant(client, "Tenant Dmarc Intruso", "intruso@dmarc.test")

    client.post(
        "/dmarc/reports",
        headers={"Authorization": f"Bearer {token_a}"},
        files={"file": ("reporte.xml", _RUA_XML, "application/xml")},
    )

    resp_intruso = client.get("/dmarc/reports", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_intruso.status_code == 200
    assert resp_intruso.json() == []


def test_get_dmarc_summary_resumen_con_remitentes_mixtos(client):
    token = _registrar_tenant(client, "Tenant Dmarc Summary", "summary@dmarc.test")
    client.post(
        "/dmarc/reports",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("reporte.xml", _RUA_XML, "application/xml")},
    )
    resp = client.get("/dmarc/summary", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    por_ip = {s["source_ip"]: s for s in data}
    assert por_ip["10.0.0.9"]["autorizado"] is False
    assert por_ip["20.1.1.1"]["autorizado"] is True


def test_get_dmarc_summary_tenant_sin_reportes_devuelve_lista_vacia(client):
    token = _registrar_tenant(client, "Tenant Dmarc Vacio", "vacio@dmarc.test")
    resp = client.get("/dmarc/summary", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_dmarc_summary_aislamiento_por_tenant(client):
    token_a = _registrar_tenant(client, "Tenant Dmarc Summary Dueno", "sdueno@dmarc.test")
    token_b = _registrar_tenant(client, "Tenant Dmarc Summary Intruso", "sintruso@dmarc.test")

    client.post(
        "/dmarc/reports",
        headers={"Authorization": f"Bearer {token_a}"},
        files={"file": ("reporte.xml", _RUA_XML, "application/xml")},
    )

    resp_intruso = client.get("/dmarc/summary", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_intruso.status_code == 200
    assert resp_intruso.json() == []
