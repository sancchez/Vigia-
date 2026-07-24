"""Fase 3b (openspec/changes/fase3b-vigilancia-continua-certstream) --
CRUD `GET/POST/DELETE /watched-brands`.

Un caso por escenario de spec.md: aislamiento por tenant, marca vacía
rechazada (422), y baja de marca ajena bloqueada (404).
"""

from __future__ import annotations


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123"):
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return token


def test_listado_aislado_por_tenant(client):
    token_a = _registrar_tenant(client, "Tenant Marcas A", "a@marcas.test")
    token_b = _registrar_tenant(client, "Tenant Marcas B", "b@marcas.test")

    for marca in ("bancoacme", "acmecorp"):
        resp = client.post(
            "/watched-brands", json={"marca": marca}, headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp.status_code == 201

    resp = client.post(
        "/watched-brands", json={"marca": "otramarca"}, headers={"Authorization": f"Bearer {token_b}"}
    )
    assert resp.status_code == 201

    listado_a = client.get("/watched-brands", headers={"Authorization": f"Bearer {token_a}"})
    listado_b = client.get("/watched-brands", headers={"Authorization": f"Bearer {token_b}"})
    assert listado_a.status_code == 200
    assert listado_b.status_code == 200

    marcas_a = {b["marca"] for b in listado_a.json()}
    marcas_b = {b["marca"] for b in listado_b.json()}
    assert marcas_a == {"bancoacme", "acmecorp"}
    assert marcas_b == {"otramarca"}
    assert all(b["is_active"] for b in listado_a.json())


def test_alta_valida_crea_marca_activa(client):
    token = _registrar_tenant(client, "Tenant Marca Alta", "alta@marcas.test")
    resp = client.post(
        "/watched-brands", json={"marca": "bancoacme"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["marca"] == "bancoacme"
    assert body["is_active"] is True
    assert "id" in body and body["id"]


def test_marca_vacia_rechazada_422(client):
    token = _registrar_tenant(client, "Tenant Marca Vacia", "vacia@marcas.test")
    resp = client.post(
        "/watched-brands", json={"marca": "   "}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 422

    listado = client.get("/watched-brands", headers={"Authorization": f"Bearer {token}"})
    assert listado.json() == []


def test_baja_de_marca_ajena_bloqueada_404(client):
    token_a = _registrar_tenant(client, "Tenant Marca Ajena A", "ajenaA@marcas.test")
    token_b = _registrar_tenant(client, "Tenant Marca Ajena B", "ajenaB@marcas.test")

    creada = client.post(
        "/watched-brands", json={"marca": "marcadeB"}, headers={"Authorization": f"Bearer {token_b}"}
    )
    assert creada.status_code == 201
    brand_id = creada.json()["id"]

    resp = client.delete(f"/watched-brands/{brand_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 404

    # la marca de B sigue intacta y activa
    listado_b = client.get("/watched-brands", headers={"Authorization": f"Bearer {token_b}"}).json()
    assert any(b["id"] == brand_id and b["is_active"] for b in listado_b)


def test_baja_propia_desactiva_la_marca(client):
    token = _registrar_tenant(client, "Tenant Marca Baja", "baja@marcas.test")
    creada = client.post(
        "/watched-brands", json={"marca": "marcaparaborrar"}, headers={"Authorization": f"Bearer {token}"}
    )
    brand_id = creada.json()["id"]

    resp = client.delete(f"/watched-brands/{brand_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204

    listado = client.get("/watched-brands", headers={"Authorization": f"Bearer {token}"}).json()
    assert not any(b["id"] == brand_id and b["is_active"] for b in listado)


def test_alta_duplicada_activa_no_crea_fila_nueva(client):
    token = _registrar_tenant(client, "Tenant Marca Dup", "dup@marcas.test")
    primera = client.post(
        "/watched-brands", json={"marca": "bancoacme"}, headers={"Authorization": f"Bearer {token}"}
    )
    segunda = client.post(
        "/watched-brands", json={"marca": "bancoacme"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert primera.status_code == 201
    assert segunda.status_code == 201
    assert primera.json()["id"] == segunda.json()["id"]

    listado = client.get("/watched-brands", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(listado) == 1
