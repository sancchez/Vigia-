"""Fase 3b (openspec/changes/fase3b-vigilancia-continua-certstream) --
CRUD `GET/POST/DELETE /watched-brands`.

Un caso por escenario de spec.md: aislamiento por tenant, marca vacía
rechazada (422), y baja de marca ajena bloqueada (404).

**Cierre del hueco de propiedad de marca (sesión posterior):** el alta ahora
exige un `dominio_asociado` que ya sea un activo tipo 'dominio' VERIFICADO
del tenant -- antes, cualquiera podía registrar "bancolombia" y alimentar
con ella la vigilancia CertStream. Todos los helpers de este archivo
registran + verifican (con `verificar_asset` mockeado, sin DNS real, mismo
patrón que `test_antisuplantacion_endpoint.py`) el dominio de respaldo antes
de dar de alta una marca.
"""

from __future__ import annotations

import pytest

import api.main as main_module


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123"):
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return token


def _crear_asset(client, token: str, valor: str) -> str:
    resp = client.post(
        "/assets",
        headers={"Authorization": f"Bearer {token}"},
        json={"tipo": "dominio", "valor": valor},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _dominio_verificado(client, token: str, valor: str, monkeypatch) -> str:
    """Registra `valor` como asset dominio del tenant y lo marca verificado
    sin tocar DNS/HTTP real."""
    asset_id = _crear_asset(client, token, valor)
    monkeypatch.setattr(
        main_module, "verificar_asset", lambda *_a, **_k: (True, "mock: token encontrado")
    )
    resp = client.post(
        f"/assets/{asset_id}/verify",
        headers={"Authorization": f"Bearer {token}"},
        json={"metodo": "dns_txt"},
    )
    assert resp.status_code == 200
    assert resp.json()["verificado"] is True
    return valor


def _alta(client, token: str, marca: str, dominio: str):
    return client.post(
        "/watched-brands",
        json={"marca": marca, "dominio_asociado": dominio},
        headers={"Authorization": f"Bearer {token}"},
    )


# ---------------------------------------------------------------------------
# Gate de propiedad: una marca debe estar anclada a un dominio verificado.
# ---------------------------------------------------------------------------


def test_marca_sin_dominio_asociado_rechazada_422(client):
    """El campo es obligatorio: un body con solo `marca` ya no valida."""
    token = _registrar_tenant(client, "Tenant Sin Dominio", "sindominio@marcas.test")
    resp = client.post(
        "/watched-brands", json={"marca": "bancolombia"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 422


def test_marca_con_dominio_no_registrado_rechazada_403(client):
    """El caso del agujero real: un tenant cualquiera intentando vigilar
    'bancolombia' apoyándose en un dominio que no es suyo."""
    token = _registrar_tenant(client, "Tenant Impostor", "impostor@marcas.test")
    resp = _alta(client, token, "bancolombia", "bancolombia.com")
    assert resp.status_code == 403
    assert "POST /assets" in resp.json()["detail"]

    listado = client.get("/watched-brands", headers={"Authorization": f"Bearer {token}"})
    assert listado.json() == []


def test_marca_con_dominio_registrado_pero_sin_verificar_rechazada_403(client):
    """Registrar el asset no basta -- hay que probar control real del dominio,
    mismo criterio que /scan, /scan/activo y /antisuplantacion."""
    token = _registrar_tenant(client, "Tenant Sin Verificar", "sinverif@marcas.test")
    _crear_asset(client, token, "miempresa.com")

    resp = _alta(client, token, "miempresa", "miempresa.com")
    assert resp.status_code == 403
    detalle = resp.json()["detail"]
    assert "NO verificado" in detalle
    assert "_vigia-challenge.miempresa.com" in detalle


def test_marca_con_dominio_de_otro_tenant_rechazada_403(client, monkeypatch):
    """El dominio verificado de A no habilita marcas de B."""
    token_a = _registrar_tenant(client, "Tenant Dueño", "dueno@marcas.test")
    token_b = _registrar_tenant(client, "Tenant Ajeno", "ajeno@marcas.test")
    _dominio_verificado(client, token_a, "miempresa.com", monkeypatch)

    resp = _alta(client, token_b, "miempresa", "miempresa.com")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# CRUD (escenarios originales de spec.md, ahora con el dominio de respaldo).
# ---------------------------------------------------------------------------


@pytest.fixture()
def tenant_con_dominio(client, monkeypatch):
    """Tenant listo para dar de alta marcas: dominio propio ya verificado."""
    token = _registrar_tenant(client, "Tenant Marcas", "marcas@marcas.test")
    dominio = _dominio_verificado(client, token, "miempresa.com", monkeypatch)
    return token, dominio


def test_listado_aislado_por_tenant(client, monkeypatch):
    token_a = _registrar_tenant(client, "Tenant Marcas A", "a@marcas.test")
    token_b = _registrar_tenant(client, "Tenant Marcas B", "b@marcas.test")
    dominio_a = _dominio_verificado(client, token_a, "empresa-a.com", monkeypatch)
    dominio_b = _dominio_verificado(client, token_b, "empresa-b.com", monkeypatch)

    for marca in ("bancoacme", "acmecorp"):
        assert _alta(client, token_a, marca, dominio_a).status_code == 201

    assert _alta(client, token_b, "otramarca", dominio_b).status_code == 201

    listado_a = client.get("/watched-brands", headers={"Authorization": f"Bearer {token_a}"})
    listado_b = client.get("/watched-brands", headers={"Authorization": f"Bearer {token_b}"})
    assert listado_a.status_code == 200
    assert listado_b.status_code == 200

    marcas_a = {b["marca"] for b in listado_a.json()}
    marcas_b = {b["marca"] for b in listado_b.json()}
    assert marcas_a == {"bancoacme", "acmecorp"}
    assert marcas_b == {"otramarca"}
    assert all(b["is_active"] for b in listado_a.json())


def test_alta_valida_crea_marca_activa_y_verificada(tenant_con_dominio, client):
    token, dominio = tenant_con_dominio
    resp = _alta(client, token, "bancoacme", dominio)
    assert resp.status_code == 201
    body = resp.json()
    assert body["marca"] == "bancoacme"
    assert body["is_active"] is True
    assert body["dominio_asociado"] == dominio
    assert body["verificada"] is True
    assert "id" in body and body["id"]


def test_marca_vacia_rechazada_422(tenant_con_dominio, client):
    token, dominio = tenant_con_dominio
    resp = _alta(client, token, "   ", dominio)
    assert resp.status_code == 422

    listado = client.get("/watched-brands", headers={"Authorization": f"Bearer {token}"})
    assert listado.json() == []


def test_baja_de_marca_ajena_bloqueada_404(client, monkeypatch):
    token_a = _registrar_tenant(client, "Tenant Marca Ajena A", "ajenaA@marcas.test")
    token_b = _registrar_tenant(client, "Tenant Marca Ajena B", "ajenaB@marcas.test")
    dominio_b = _dominio_verificado(client, token_b, "empresa-b.com", monkeypatch)

    creada = _alta(client, token_b, "marcadeB", dominio_b)
    assert creada.status_code == 201
    brand_id = creada.json()["id"]

    resp = client.delete(f"/watched-brands/{brand_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 404

    # la marca de B sigue intacta y activa
    listado_b = client.get("/watched-brands", headers={"Authorization": f"Bearer {token_b}"}).json()
    assert any(b["id"] == brand_id and b["is_active"] for b in listado_b)


def test_baja_propia_desactiva_la_marca(tenant_con_dominio, client):
    token, dominio = tenant_con_dominio
    creada = _alta(client, token, "marcaparaborrar", dominio)
    brand_id = creada.json()["id"]

    resp = client.delete(f"/watched-brands/{brand_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204

    listado = client.get("/watched-brands", headers={"Authorization": f"Bearer {token}"}).json()
    assert not any(b["id"] == brand_id and b["is_active"] for b in listado)


def test_alta_duplicada_activa_no_crea_fila_nueva(tenant_con_dominio, client):
    token, dominio = tenant_con_dominio
    primera = _alta(client, token, "bancoacme", dominio)
    segunda = _alta(client, token, "bancoacme", dominio)
    assert primera.status_code == 201
    assert segunda.status_code == 201
    assert primera.json()["id"] == segunda.json()["id"]

    listado = client.get("/watched-brands", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(listado) == 1


def test_marca_legada_sin_dominio_sale_como_no_verificada_y_se_puede_curar(
    tenant_con_dominio, client
):
    """Datos previos a la migración: la fila se conserva y se lista, marcada
    `verificada=false`; re-registrarla con un dominio verificado la ancla sin
    duplicarla (ver db/schema.sql)."""
    import uuid

    from db.connection import get_conn

    token, dominio = tenant_con_dominio
    tenant_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["tenant_id"]

    conn = get_conn()
    try:
        brand_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO watched_brands (id, tenant_id, marca, is_active) VALUES (?, ?, ?, 1)",
            (brand_id, tenant_id, "marcalegada"),
        )
        conn.commit()
    finally:
        conn.close()

    listado = client.get("/watched-brands", headers={"Authorization": f"Bearer {token}"}).json()
    legada = next(b for b in listado if b["id"] == brand_id)
    assert legada["verificada"] is False
    assert legada["dominio_asociado"] is None

    curada = _alta(client, token, "marcalegada", dominio)
    assert curada.status_code == 201
    assert curada.json()["id"] == brand_id
    assert curada.json()["verificada"] is True
