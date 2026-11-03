"""Tests de los chequeos de detección dirigida (`tools/deteccion_dirigida.py`).

Estos chequeos son escaneo ACTIVO de alta confianza: envían tráfico de ataque
y solo reportan con evidencia concreta de explotación. La regla de calidad no
negociable del proyecto es "sin falsos positivos, sin dar falsos verdes", así
que CADA chequeo se prueba en dos escenarios:

  1. App VULNERABLE (fake tipo Juice Shop) -> el chequeo DEBE disparar.
  2. App SEGURA/parchada (fake que rechaza los ataques) -> el chequeo NO debe
     disparar. Estos casos negativos son la defensa real contra falsos
     positivos: si el ataque falla o el control se comporta como se espera de
     una app segura, no se reporta nada.

No se toca la red: se inyecta una `session` falsa que simula las respuestas
HTTP de cada app.
"""

from __future__ import annotations

import base64
import json

import pytest

from tools import deteccion_dirigida as dd


# ---------------------------------------------------------------------------
# Infraestructura de fakes HTTP (sin red)
# ---------------------------------------------------------------------------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _make_jwt(uid: int, email: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"data": {"id": uid, "email": email}}).encode())
    firma = _b64url(b"firma-valida-simulada")
    return f"{header}.{payload}.{firma}"


class _Resp:
    def __init__(self, status: int, json_data=None, text: str | None = None):
        self.status_code = status
        self._json = json_data
        if text is not None:
            self.text = text
        elif json_data is not None:
            self.text = json.dumps(json_data)
        else:
            self.text = ""

    def json(self):
        if self._json is None:
            raise ValueError("respuesta sin cuerpo JSON")
        return self._json


def _uid_de_token(token: str):
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        return payload.get("data", {}).get("id")
    except Exception:  # noqa: BLE001
        return None


def _alg_de_token(token: str):
    try:
        header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
        return header.get("alg")
    except Exception:  # noqa: BLE001
        return None


class JuiceShopVulnerableFake:
    """Simula un objetivo vulnerable (Juice Shop) para los 6 chequeos."""

    def __init__(self):
        self._uid = 100
        self._registrados = {}  # email -> uid

    def close(self):
        pass

    def post(self, url, json=None, timeout=None):  # noqa: A002
        json = json or {}
        if url.endswith("/api/Users/"):
            self._uid += 1
            self._registrados[json.get("email")] = self._uid
            return _Resp(201, {"data": {"id": self._uid}})
        if url.endswith("/rest/user/login"):
            email = json.get("email", "")
            if "' OR" in email or "--" in email:  # SQLi bypass -> autentica
                return _Resp(200, {"authentication": {"token": _make_jwt(1, "admin@juice-sh.op"), "bid": 1}})
            uid = self._registrados.get(email)
            if uid is None:  # credenciales desconocidas -> rechazo
                return _Resp(401, {"error": "invalid"})
            return _Resp(200, {"authentication": {"token": _make_jwt(uid, email), "bid": uid}})
        return _Resp(404)

    def get(self, url, headers=None, timeout=None):
        headers = headers or {}
        auth = headers.get("Authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""

        if "/rest/basket/" in url:
            bid = url.rsplit("/", 1)[-1]
            # Sin control de acceso: devuelve la cesta pedida, su dueño es el
            # usuario con ese uid (bid == uid en el fake).
            return _Resp(200, {"status": "success", "data": {"id": int(bid), "UserId": int(bid)}})

        if url.endswith("/rest/user/authentication-details"):
            if not token:
                return _Resp(401)
            # Basura -> rechazo. Token válido O forjado alg:none -> acepta (roto).
            if _uid_de_token(token) is None and _alg_de_token(token) != "none":
                return _Resp(401)
            usuarios = [{"id": i, "email": f"u{i}@x.com"} for i in range(1, 39)]
            return _Resp(200, {"data": usuarios})

        if url.endswith("/ftp/"):
            listado = "package.json.bak coupons_2013.md.bak acquisitions.md eastere.gg .bak"
            return _Resp(200, text=listado)
        if url.endswith("/ftp/package.json.bak"):
            return _Resp(403)
        if url.endswith("/ftp/package.json.bak%2500.md"):
            return _Resp(200, text='{"name":"juice-shop","dependencies":{}}')
        return _Resp(404)


class AppSeguraFake:
    """Simula un objetivo parchado: rechaza todos los ataques dirigidos."""

    def __init__(self):
        self._uid = 200
        self._registrados = {}

    def close(self):
        pass

    def post(self, url, json=None, timeout=None):  # noqa: A002
        json = json or {}
        if url.endswith("/api/Users/"):
            self._uid += 1
            self._registrados[json.get("email")] = self._uid
            return _Resp(201, {"data": {"id": self._uid}})
        if url.endswith("/rest/user/login"):
            email = json.get("email", "")
            if "' OR" in email or "--" in email:  # consulta parametrizada -> sin bypass
                return _Resp(401)
            uid = self._registrados.get(email)
            if uid is None:
                return _Resp(401)
            return _Resp(200, {"authentication": {"token": _make_jwt(uid, email), "bid": uid}})
        return _Resp(404)

    def get(self, url, headers=None, timeout=None):
        headers = headers or {}
        auth = headers.get("Authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""

        if "/rest/basket/" in url:
            bid = url.rsplit("/", 1)[-1]
            uid = _uid_de_token(token)
            # Control de acceso: solo la cesta propia; cualquier otra -> 401.
            if uid is not None and str(uid) == str(bid):
                return _Resp(200, {"data": {"id": int(bid), "UserId": int(uid)}})
            return _Resp(401)

        if url.endswith("/rest/user/authentication-details"):
            if not token:
                return _Resp(401)
            # Firma verificada: alg:none y basura rechazados. Usuario normal no
            # es admin -> 403 (no ve datos de otros).
            if _alg_de_token(token) == "none" or _uid_de_token(token) is None:
                return _Resp(401)
            return _Resp(403)

        if url.endswith("/ftp/"):
            return _Resp(404)
        if url.endswith("/ftp/package.json.bak"):
            return _Resp(404)
        if url.endswith("/ftp/package.json.bak%2500.md"):
            return _Resp(404)
        return _Resp(404)


BASE = "http://target.lab"


# ---------------------------------------------------------------------------
# Positivos: app vulnerable -> cada chequeo dispara con la evidencia correcta
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "funcion, tipo_esperado, ubicacion_esperada",
    [
        (dd.check_sqli_login, "sql_injection", "/rest/user/login"),
        (dd.check_idor_basket, "broken_access_control", "/rest/basket/{id}"),
        (dd.check_admin_broken_access, "broken_access_control", "/#/administration"),
        (dd.check_jwt_alg_none, "jwt_vuln", "/rest/user/login"),
        (dd.check_ftp_exposure, "sensitive_data_exposure", "/ftp/"),
        (dd.check_path_traversal_ftp, "path_traversal", "/ftp/:file"),
    ],
)
def test_chequeo_dispara_contra_app_vulnerable(funcion, tipo_esperado, ubicacion_esperada):
    h = funcion(BASE, JuiceShopVulnerableFake(), timeout=5)
    assert h is not None, "el chequeo debía disparar contra la app vulnerable"
    assert h.tipo == tipo_esperado
    assert h.ubicacion == ubicacion_esperada
    assert h.evidencia, "todo hallazgo debe traer evidencia concreta"


# ---------------------------------------------------------------------------
# Negativos (anti-falso-positivo): app segura -> ningún chequeo dispara
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "funcion",
    [
        dd.check_sqli_login,
        dd.check_idor_basket,
        dd.check_admin_broken_access,
        dd.check_jwt_alg_none,
        dd.check_ftp_exposure,
        dd.check_path_traversal_ftp,
    ],
)
def test_chequeo_no_dispara_contra_app_segura(funcion):
    h = funcion(BASE, AppSeguraFake(), timeout=5)
    assert h is None, "el chequeo NO debe reportar contra una app parchada (falso positivo)"


# ---------------------------------------------------------------------------
# Casos negativos adicionales (fallos de red / respuestas degeneradas)
# ---------------------------------------------------------------------------

class SesionQueFalla:
    def close(self):
        pass

    def get(self, *a, **k):
        import requests
        raise requests.RequestException("red caída")

    def post(self, *a, **k):
        import requests
        raise requests.RequestException("red caída")


@pytest.mark.parametrize("funcion", [f for _, f in dd.CHEQUEOS])
def test_chequeo_degrada_con_gracia_ante_error_de_red(funcion):
    # Ningún chequeo debe lanzar hacia el llamador si la red falla.
    assert funcion(BASE, SesionQueFalla(), timeout=1) is None


def test_login_que_autentica_todo_no_se_atribuye_a_sqli():
    """Si el control (credenciales inválidas) YA devuelve token, la app tiene
    login roto de otra clase, no SQLi -- no debe atribuirse a inyección."""

    class LoginAbierto:
        def close(self):
            pass

        def post(self, url, json=None, timeout=None):  # noqa: A002
            if url.endswith("/rest/user/login"):
                return _Resp(200, {"authentication": {"token": _make_jwt(1, "x@x.com"), "bid": 1}})
            return _Resp(404)

        def get(self, *a, **k):
            return _Resp(404)

    assert dd.check_sqli_login(BASE, LoginAbierto(), timeout=5) is None


def test_orquestador_devuelve_los_seis_contra_app_vulnerable():
    hallazgos = dd.ejecutar_checks_dirigidos(BASE, session=JuiceShopVulnerableFake(), timeout=5)
    tipos = sorted(h.tipo for h in hallazgos)
    assert tipos == sorted([
        "sql_injection",
        "broken_access_control",
        "broken_access_control",
        "jwt_vuln",
        "sensitive_data_exposure",
        "path_traversal",
    ])


def test_orquestador_no_reporta_nada_contra_app_segura():
    assert dd.ejecutar_checks_dirigidos(BASE, session=AppSeguraFake(), timeout=5) == []
