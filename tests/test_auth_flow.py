"""Flujo end-to-end de autenticación: `POST /auth/register` ->
`POST /auth/login` -> `GET /me`, contra la app real de `api/main.py` con
`TestClient` (in-process, sin necesidad de un servidor uvicorn corriendo)
y una base SQLite temporal (nunca `ciberseguridad.db` -- ver
`tests/conftest.py::test_db`).

Es la base de la que depende literalmente cada otro endpoint del producto
(`Depends(get_current_user)` en todos menos `/auth/register`,
`/auth/login` y `/health` -- ver docs/produccion-readiness.md, "Sanity
check de auth en endpoints nuevos"): si esto se rompe, rompe todo el
producto a la vez.
"""

from __future__ import annotations


def test_registro_login_y_me_devuelven_datos_consistentes(client):
    registro = client.post(
        "/auth/register",
        json={
            "nombre_negocio": "Pyme De Prueba S.A.S.",
            "email": "owner@pymeprueba.test",
            "password": "unaClaveSegura123",
        },
    )
    assert registro.status_code == 200
    token_registro = registro.json()["access_token"]
    assert token_registro

    login = client.post(
        "/auth/login",
        json={"email": "owner@pymeprueba.test", "password": "unaClaveSegura123"},
    )
    assert login.status_code == 200
    token_login = login.json()["access_token"]
    assert token_login

    me = client.get("/me", headers={"Authorization": f"Bearer {token_login}"})
    assert me.status_code == 200
    cuerpo = me.json()
    assert cuerpo["email"] == "owner@pymeprueba.test"
    assert cuerpo["tenant_nombre"] == "Pyme De Prueba S.A.S."
    assert cuerpo["role"] == "owner"
    assert cuerpo["plan"] == "trial"
    assert cuerpo["tenant_id"]
    assert cuerpo["user_id"]


def test_registro_duplicado_devuelve_409(client):
    payload = {
        "nombre_negocio": "Pyme Duplicada",
        "email": "duplicado@pymeprueba.test",
        "password": "unaClaveSegura123",
    }
    primero = client.post("/auth/register", json=payload)
    assert primero.status_code == 200

    segundo = client.post("/auth/register", json=payload)
    assert segundo.status_code == 409


def test_login_con_password_incorrecto_devuelve_401(client):
    client.post(
        "/auth/register",
        json={
            "nombre_negocio": "Otra Pyme",
            "email": "otro@pymeprueba.test",
            "password": "claveCorrecta123",
        },
    )
    resp = client.post(
        "/auth/login",
        json={"email": "otro@pymeprueba.test", "password": "claveIncorrecta"},
    )
    assert resp.status_code == 401


def test_me_sin_token_devuelve_401_o_403(client):
    resp = client.get("/me")
    assert resp.status_code in (401, 403)


def test_me_con_token_invalido_devuelve_401(client):
    resp = client.get("/me", headers={"Authorization": "Bearer token-invalido-de-mentira"})
    assert resp.status_code == 401
