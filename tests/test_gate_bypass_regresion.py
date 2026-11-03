"""Regresión de los bypasses reales del gate de autorización de escaneo.

Cada test de este archivo reproduce un PoC ejecutable de la auditoría de
seguridad -- no prueba "que la función existe", prueba que el ATAQUE
concreto ya no funciona. Si alguno de estos vuelve a pasar en verde con el
gate roto, es que el test estaba mal escrito.

Cobertura:

- **C-1** -- `POST /scan` validaba solo `payload.target`, mientras
  `agents/escaneo.py` escanea `scope.dominios`. Un tenant con un dominio
  propio verificado podía escanear activamente (Nuclei + ZAP) el dominio de
  un tercero metiéndolo en el scope. Además `scope.codigo_paths` daba
  lectura de rutas arbitrarias del servidor vía Semgrep y `scope.imagenes`
  un `docker pull` arbitrario.
- **C-2** -- toda IP privada/loopback/link-local estaba EXENTA de
  verificación de propiedad, en cualquier entorno: SSRF al metadata service
  del cloud (169.254.169.254) con solo registrar un asset.
- **C-3** -- `JWT_SECRET` ausente caía a un literal hardcodeado del
  repositorio.
- **H-4** -- `assets.autorizacion_activa` se leía y nunca se escribía.
- **M-7** -- los cuatro caminos de ejecución comparten el mismo gate.
"""

from __future__ import annotations

import pytest

import agents.recon as recon_module
import api.main as main_module
from db.connection import get_conn
from tools._shared import ToolExecutionError


def _registrar_tenant(client, nombre: str, email: str, password: str = "claveSegura123") -> str:
    resp = client.post(
        "/auth/register",
        json={"nombre_negocio": nombre, "email": email, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _registrar_asset_verificado(client, token: str, valor: str, tipo: str = "dominio") -> str:
    """Crea el asset y lo marca verificado directo en la DB.

    Se escribe `verificado = 1` a mano en vez de pasar por
    `POST /assets/{id}/verify` porque ese endpoint hace una consulta DNS/HTTP
    real contra el dominio -- lo que se prueba acá es el gate de scope, no el
    mecanismo de verificación (que ya tiene su propia batería en
    tests/test_asset_verification.py).
    """
    resp = client.post("/assets", headers=_headers(token), json={"tipo": tipo, "valor": valor})
    assert resp.status_code == 200
    asset_id = resp.json()["id"]
    conn = get_conn()
    try:
        conn.execute("UPDATE assets SET verificado = 1 WHERE id = ?", (asset_id,))
        conn.commit()
    finally:
        conn.close()
    return asset_id


@pytest.fixture()
def _sin_herramientas_reales(monkeypatch):
    """Ninguna herramienta externa (recon o escaneo) corre de verdad en estos tests.

    Además de evitar red real, es lo que convierte a estos tests en una
    trampa útil: `run_nuclei`/`run_zap_baseline` levantan `AssertionError` si
    llegan a invocarse, así que un gate roto no falla con un assert genérico
    al final -- falla exactamente en el punto donde el escaneo tocó
    infraestructura ajena.
    """

    def _falla_rapido(*_a, **_k):
        raise ToolExecutionError("mock de test: binario no disponible")

    monkeypatch.setattr(recon_module, "run_subfinder", _falla_rapido)
    monkeypatch.setattr(recon_module, "run_amass_passive", _falla_rapido)


# ---------------------------------------------------------------------------
# C-1 -- el PoC original: scope.dominios se saltaba el gate por completo.
# ---------------------------------------------------------------------------


def test_c1_scope_dominios_de_un_tercero_es_rechazado_con_403(
    client, monkeypatch, _sin_herramientas_reales
):
    """PoC C-1 reproducido tal cual: asset propio verificado en `target`,
    dominio de un tercero NO verificado en `scope.dominios`.

    Antes del fix esto devolvía 200 y `agents/escaneo.py` le pasaba
    'victima-ajena.test' a Nuclei y a ZAP. Ahora es 403 y ningún wrapper de
    `tools/scan.py` llega a ejecutarse.
    """
    import agents.escaneo as escaneo_module

    def _nunca(*_a, **_k):
        raise AssertionError(
            "run_nuclei/run_zap_baseline se invocó -- el gate de scope NO bloqueó el escaneo"
        )

    monkeypatch.setattr(escaneo_module, "run_nuclei", _nunca)
    monkeypatch.setattr(escaneo_module, "run_zap_baseline", _nunca)

    token = _registrar_tenant(client, "Pyme C1", "c1@bypass.test")
    _registrar_asset_verificado(client, token, "mipropiodominio.test")

    resp = client.post(
        "/scan",
        headers=_headers(token),
        json={
            "target": "mipropiodominio.test",
            "autorizacion_firmada": True,
            "scope": {"dominios": ["victima-ajena.test"]},
        },
    )

    assert resp.status_code == 403
    detalle = resp.json()["detail"]
    assert "scope.dominios" in detalle
    assert "victima-ajena.test" in detalle


def test_c1_scope_ips_de_un_tercero_es_rechazado_con_403(client, _sin_herramientas_reales):
    """Misma puerta para `scope.ips` -- una IP pública ajena tampoco entra por el scope."""
    token = _registrar_tenant(client, "Pyme C1 IP", "c1ip@bypass.test")
    _registrar_asset_verificado(client, token, "mipropiodominio2.test")

    resp = client.post(
        "/scan",
        headers=_headers(token),
        json={
            "target": "mipropiodominio2.test",
            "autorizacion_firmada": True,
            "scope": {"ips": ["8.8.8.8"]},
        },
    )
    assert resp.status_code == 403
    assert "8.8.8.8" in resp.json()["detail"]


def test_c1_scope_apps_de_un_tercero_es_rechazado_con_403(client, _sin_herramientas_reales):
    token = _registrar_tenant(client, "Pyme C1 App", "c1app@bypass.test")
    _registrar_asset_verificado(client, token, "mipropiodominio3.test")

    resp = client.post(
        "/scan",
        headers=_headers(token),
        json={
            "target": "mipropiodominio3.test",
            "autorizacion_firmada": True,
            "scope": {"apps": ["https://panel.victima-ajena.test/login"]},
        },
    )
    assert resp.status_code == 403
    assert "scope.apps" in resp.json()["detail"]


def test_c1_scope_con_solo_activos_propios_verificados_sigue_funcionando(
    client, monkeypatch, _sin_herramientas_reales
):
    """El gate no puede ser un "rechaza todo": un scope legítimo debe pasar.

    Dos assets propios verificados, uno en `target` y otro en
    `scope.dominios` -> 200. Sin este test, un fix que devolviera 403 siempre
    también pasaría los tests de arriba.
    """
    import agents.escaneo as escaneo_module

    objetivos_escaneados = []

    class _ResultadoFalso:
        findings: list = []

    def _nuclei(objetivo, **_k):
        objetivos_escaneados.append(objetivo)
        return _ResultadoFalso()

    monkeypatch.setattr(escaneo_module, "run_nuclei", _nuclei)
    monkeypatch.setattr(
        escaneo_module, "run_zap_baseline", lambda *_a, **_k: _ResultadoFalso()
    )

    token = _registrar_tenant(client, "Pyme C1 OK", "c1ok@bypass.test")
    _registrar_asset_verificado(client, token, "principal-propio.test")
    _registrar_asset_verificado(client, token, "secundario-propio.test")

    resp = client.post(
        "/scan",
        headers=_headers(token),
        json={
            "target": "principal-propio.test",
            "autorizacion_firmada": True,
            "scope": {"dominios": ["principal-propio.test", "secundario-propio.test"]},
        },
    )
    assert resp.status_code == 200
    assert set(objetivos_escaneados) == {"principal-propio.test", "secundario-propio.test"}


def test_c1_subdominio_de_un_asset_propio_es_aceptado_en_el_scope(
    client, monkeypatch, _sin_herramientas_reales
):
    """El gate reusa el matching por dominio registrable ya existente:
    'app.principal.test' cae bajo el asset 'principal.test'."""
    import agents.escaneo as escaneo_module

    class _ResultadoFalso:
        findings: list = []

    monkeypatch.setattr(escaneo_module, "run_nuclei", lambda *_a, **_k: _ResultadoFalso())
    monkeypatch.setattr(escaneo_module, "run_zap_baseline", lambda *_a, **_k: _ResultadoFalso())

    token = _registrar_tenant(client, "Pyme C1 Sub", "c1sub@bypass.test")
    _registrar_asset_verificado(client, token, "principal.test")

    resp = client.post(
        "/scan",
        headers=_headers(token),
        json={
            "target": "principal.test",
            "autorizacion_firmada": True,
            "scope": {"dominios": ["app.principal.test"]},
        },
    )
    assert resp.status_code == 200


def test_c1_codigo_paths_ya_no_es_aceptable_desde_http(client, _sin_herramientas_reales):
    """`scope.codigo_paths` daba lectura arbitraria del filesystem del servidor
    vía Semgrep. Sacado del modelo Pydantic: ahora es un 422, no un campo
    ignorado en silencio (que dejaría al atacante sin saber que falló, pero
    también al cliente legítimo creyendo que se escaneó su código)."""
    token = _registrar_tenant(client, "Pyme C1 SAST", "c1sast@bypass.test")
    _registrar_asset_verificado(client, token, "propio-sast.test")

    resp = client.post(
        "/scan",
        headers=_headers(token),
        json={
            "target": "propio-sast.test",
            "autorizacion_firmada": True,
            "scope": {"codigo_paths": ["C:/Users", "/etc"]},
        },
    )
    assert resp.status_code == 422


def test_c1_imagenes_ya_no_es_aceptable_desde_http(client, _sin_herramientas_reales):
    """`scope.imagenes` implicaba un `docker pull` de una referencia arbitraria."""
    token = _registrar_tenant(client, "Pyme C1 SCA", "c1sca@bypass.test")
    _registrar_asset_verificado(client, token, "propio-sca.test")

    resp = client.post(
        "/scan",
        headers=_headers(token),
        json={
            "target": "propio-sca.test",
            "autorizacion_firmada": True,
            "scope": {"imagenes": ["atacante/imagen-maliciosa:latest"]},
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# C-2 -- SSRF vía la exención incondicional de IP privada/link-local.
# ---------------------------------------------------------------------------


@pytest.fixture()
def _entorno_produccion(monkeypatch):
    """Quita la declaración de entorno local que pone `tests/conftest.py`.

    Con esto el código corre exactamente como en un despliegue real: sin
    `VIGIA_ENTORNO` ni `VIGIA_ALLOW_PRIVATE_TARGETS`.
    """
    monkeypatch.delenv("VIGIA_ENTORNO", raising=False)
    monkeypatch.delenv("VIGIA_ALLOW_PRIVATE_TARGETS", raising=False)


@pytest.mark.parametrize(
    "tipo,valor",
    [
        ("ip", "169.254.169.254"),  # metadata service AWS/GCP/Azure
        ("ip", "169.254.1.1"),
        ("dominio", "http://169.254.169.254/latest/meta-data/"),
        ("app", "169.254.169.254:80"),
    ],
)
def test_c2_link_local_esta_prohibido_incluso_en_entorno_local(tipo, valor):
    """Link-local NUNCA se exime ni se autoriza -- ni siquiera con
    `VIGIA_ENTORNO=local` (que es lo que la suite declara).

    Es la única regla del módulo sin escape por variable de entorno: el
    metadata service del cloud no es un "activo del cliente" en ningún
    escenario legítimo, y dejarle una puerta configurable es dejarle una
    puerta.
    """
    from tools.asset_verification import (
        asset_autorizado_para_escanear,
        es_target_exento_de_verificacion,
        es_target_prohibido,
    )

    prohibido, motivo = es_target_prohibido(tipo, valor)
    assert prohibido is True
    assert "link-local" in motivo
    assert es_target_exento_de_verificacion(tipo, valor) is False
    # Ni siquiera marcarlo `verificado=True` a mano en la DB lo desbloquea.
    assert asset_autorizado_para_escanear(tipo, valor, verificado=True) is False


@pytest.mark.parametrize(
    "tipo,valor",
    [
        ("ip", "10.0.0.5"),
        ("ip", "192.168.1.50"),
        ("ip", "172.16.0.9"),
        ("ip", "127.0.0.1"),
        ("dominio", "localhost"),
        ("dominio", "http://192.168.0.10:8080"),
        ("app", "localhost:3000"),
    ],
)
def test_c2_ip_privada_y_localhost_no_estan_exentos_en_produccion(
    tipo, valor, _entorno_produccion
):
    """Sin entorno local declarado, la exención desaparece: fail closed.

    Antes del fix, `es_target_exento_de_verificacion` devolvía True para todo
    esto SIEMPRE, así que registrar `10.0.0.5` como asset bastaba para que el
    motor escaneara la red interna del propio despliegue.
    """
    from tools.asset_verification import (
        asset_autorizado_para_escanear,
        es_target_exento_de_verificacion,
        es_target_prohibido,
    )

    assert es_target_prohibido(tipo, valor)[0] is True
    assert es_target_exento_de_verificacion(tipo, valor) is False
    assert asset_autorizado_para_escanear(tipo, valor, verificado=False) is False
    assert asset_autorizado_para_escanear(tipo, valor, verificado=True) is False


@pytest.mark.parametrize(
    "tipo,valor",
    [
        ("ip", "10.0.0.5"),
        ("ip", "127.0.0.1"),
        ("dominio", "localhost"),
    ],
)
def test_c2_ip_privada_sigue_exenta_en_entorno_local_declarado(tipo, valor):
    """El flujo de laboratorio (Juice Shop/DVWA en Docker) no se rompe.

    `tests/conftest.py` declara `VIGIA_ENTORNO=local`, igual que haría una
    máquina de desarrollo -- ese es el único modo en que la exención vive.
    """
    from tools.asset_verification import es_target_exento_de_verificacion

    assert es_target_exento_de_verificacion(tipo, valor) is True


def test_c2_allow_private_targets_habilita_sin_declarar_entorno_local(monkeypatch):
    """La segunda variable (`VIGIA_ALLOW_PRIVATE_TARGETS`) existe para un CI que
    quiere escanear contenedores locales pero mantener el resto en modo
    producción (ej. `JWT_SECRET` obligatorio)."""
    from tools.asset_verification import es_target_exento_de_verificacion

    monkeypatch.delenv("VIGIA_ENTORNO", raising=False)
    assert es_target_exento_de_verificacion("ip", "10.0.0.5") is False
    monkeypatch.setenv("VIGIA_ALLOW_PRIVATE_TARGETS", "true")
    assert es_target_exento_de_verificacion("ip", "10.0.0.5") is True


def test_c2_scan_rechaza_target_link_local_end_to_end(client, _sin_herramientas_reales):
    """El PoC completo por HTTP: registrar el metadata service como activo y
    lanzarle un scan. 403, aunque la suite corra en modo local."""
    token = _registrar_tenant(client, "Pyme C2", "c2@bypass.test")
    resp = client.post(
        "/assets", headers=_headers(token), json={"tipo": "ip", "valor": "169.254.169.254"}
    )
    assert resp.status_code == 200
    assert resp.json()["exento_de_verificacion"] is False

    resp = client.post(
        "/scan",
        headers=_headers(token),
        json={"target": "169.254.169.254", "autorizacion_firmada": True},
    )
    assert resp.status_code == 403


def test_c2_scan_rechaza_link_local_metido_por_el_scope(client, _sin_herramientas_reales):
    """Combinación C-1 + C-2: el metadata service escondido en `scope.ips`
    detrás de un target propio y verificado."""
    token = _registrar_tenant(client, "Pyme C2 Scope", "c2scope@bypass.test")
    _registrar_asset_verificado(client, token, "propio-c2.test")

    resp = client.post(
        "/scan",
        headers=_headers(token),
        json={
            "target": "propio-c2.test",
            "autorizacion_firmada": True,
            "scope": {"ips": ["169.254.169.254"]},
        },
    )
    assert resp.status_code == 403


def test_c2_scheduler_omite_activo_link_local(monkeypatch, test_db):
    """M-7: el cuarto camino (el ciclo recurrente) usa el mismo gate.

    Un activo link-local marcado `verificado=1` a mano en la DB no debe
    recibir ni recon pasivo automático.
    """
    import api.scheduler as scheduler_module

    conn = get_conn()
    try:
        conn.execute("INSERT INTO tenants (id, slug, name) VALUES ('t-c2', 't-c2', 'T C2')")
        conn.execute(
            "INSERT INTO assets (id, tenant_id, tipo, valor, verificado) "
            "VALUES ('a-c2', 't-c2', 'ip', '169.254.169.254', 1)"
        )
        conn.commit()
    finally:
        conn.close()

    def _nunca(*_a, **_k):
        raise AssertionError("el scheduler procesó un activo link-local")

    monkeypatch.setattr(scheduler_module, "_ejecutar_ciclo_pasivo", _nunca)
    monkeypatch.setattr(scheduler_module, "_ejecutar_ciclo_activo", _nunca)

    assert scheduler_module.run_scan_cycle_once() == 0


# ---------------------------------------------------------------------------
# C-3 -- JWT_SECRET con fallback hardcodeado.
# ---------------------------------------------------------------------------


def test_c3_sin_jwt_secret_en_produccion_revienta(monkeypatch):
    from auth.jwt_auth import _jwt_secret, verificar_configuracion_jwt

    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("VIGIA_ENTORNO", raising=False)

    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        _jwt_secret()
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        verificar_configuracion_jwt()


def test_c3_el_lifespan_no_deja_arrancar_la_app_sin_secreto(monkeypatch):
    """El chequeo está cableado al arranque real, no solo disponible como función.

    Se ejercita `TestClient(app)` como context manager -- lo único que
    dispara el `lifespan` (ver el docstring de la fixture `client` en
    conftest.py, que deliberadamente NO lo hace).
    """
    from fastapi.testclient import TestClient

    from api.main import app

    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("VIGIA_ENTORNO", raising=False)

    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        with TestClient(app):
            pass


def test_c3_en_entorno_local_declarado_usa_el_secreto_de_dev(monkeypatch):
    from auth.jwt_auth import _SECRETO_DEV, _jwt_secret

    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("VIGIA_ENTORNO", "local")
    assert _jwt_secret() == _SECRETO_DEV


def test_c3_jwt_secret_configurado_gana_siempre(monkeypatch):
    from auth.jwt_auth import _jwt_secret

    monkeypatch.setenv("JWT_SECRET", "un-secreto-real-de-produccion")
    monkeypatch.delenv("VIGIA_ENTORNO", raising=False)
    assert _jwt_secret() == "un-secreto-real-de-produccion"


# ---------------------------------------------------------------------------
# H-4 -- autorizacion_activa se leía y nunca se escribía.
# ---------------------------------------------------------------------------


def test_h4_owner_puede_registrar_autorizacion_activa_con_evidencia(client):
    token = _registrar_tenant(client, "Pyme H4", "h4@bypass.test")
    asset_id = _registrar_asset_verificado(client, token, "autorizado-h4.test")

    resp = client.post(
        f"/assets/{asset_id}/autorizacion-activa",
        headers=_headers(token),
        json={
            "activa": True,
            "autorizacion_doc_url": "https://drive.test/autorizacion-firmada.pdf",
            "autorizado_por": "Ana Gerente <ana@autorizado-h4.test>",
        },
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["autorizacion_activa"] is True
    assert cuerpo["autorizacion_doc_url"] == "https://drive.test/autorizacion-firmada.pdf"
    assert cuerpo["autorizado_por"].startswith("Ana Gerente")
    assert cuerpo["autorizado_en"] is not None

    # Las cuatro columnas quedaron escritas de verdad en la DB.
    conn = get_conn()
    try:
        fila = conn.execute(
            "SELECT autorizacion_activa, autorizacion_doc_url, autorizado_por, autorizado_en "
            "FROM assets WHERE id = ?",
            (asset_id,),
        ).fetchone()
    finally:
        conn.close()
    assert fila["autorizacion_activa"] == 1
    assert fila["autorizacion_doc_url"]
    assert fila["autorizado_por"]
    assert fila["autorizado_en"]


def test_h4_activar_sin_evidencia_es_422(client):
    token = _registrar_tenant(client, "Pyme H4 B", "h4b@bypass.test")
    asset_id = _registrar_asset_verificado(client, token, "sinevidencia-h4.test")

    resp = client.post(
        f"/assets/{asset_id}/autorizacion-activa",
        headers=_headers(token),
        json={"activa": True},
    )
    assert resp.status_code == 422


def test_h4_activar_sobre_asset_no_verificado_es_403(client):
    """No se puede autorizar escaneo activo de algo cuya propiedad no se probó."""
    token = _registrar_tenant(client, "Pyme H4 C", "h4c@bypass.test")
    resp = client.post(
        "/assets", headers=_headers(token), json={"tipo": "dominio", "valor": "noverificado-h4.test"}
    )
    asset_id = resp.json()["id"]

    resp = client.post(
        f"/assets/{asset_id}/autorizacion-activa",
        headers=_headers(token),
        json={
            "activa": True,
            "autorizacion_doc_url": "https://drive.test/doc.pdf",
            "autorizado_por": "Alguien",
        },
    )
    assert resp.status_code == 403


def test_h4_revocar_limpia_la_evidencia(client):
    token = _registrar_tenant(client, "Pyme H4 D", "h4d@bypass.test")
    asset_id = _registrar_asset_verificado(client, token, "revocable-h4.test")
    client.post(
        f"/assets/{asset_id}/autorizacion-activa",
        headers=_headers(token),
        json={
            "activa": True,
            "autorizacion_doc_url": "https://drive.test/doc.pdf",
            "autorizado_por": "Ana",
        },
    )

    resp = client.post(
        f"/assets/{asset_id}/autorizacion-activa",
        headers=_headers(token),
        json={"activa": False},
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["autorizacion_activa"] is False
    assert cuerpo["autorizacion_doc_url"] is None
    assert cuerpo["autorizado_por"] is None
    assert cuerpo["autorizado_en"] is None


def test_h4_un_member_no_puede_autorizar_escaneo_activo(client):
    """Solo el `owner`. Comprometer a la empresa con una autorización de
    pruebas de intrusión no es una acción de cualquier miembro del equipo."""
    token_owner = _registrar_tenant(client, "Pyme H4 E", "h4e@bypass.test")
    asset_id = _registrar_asset_verificado(client, token_owner, "rolgate-h4.test")

    inv = client.post(
        "/tenant/invitations",
        headers=_headers(token_owner),
        json={"email": "member-h4@bypass.test", "role": "member"},
    )
    assert inv.status_code in (200, 201)
    token_invitacion = inv.json()["token"]
    reg = client.post(
        "/auth/register",
        json={
            "nombre_negocio": "",
            "email": "member-h4@bypass.test",
            "password": "claveSegura123",
            "invite_token": token_invitacion,
        },
    )
    assert reg.status_code == 200
    token_member = reg.json()["access_token"]

    resp = client.post(
        f"/assets/{asset_id}/autorizacion-activa",
        headers=_headers(token_member),
        json={
            "activa": True,
            "autorizacion_doc_url": "https://drive.test/doc.pdf",
            "autorizado_por": "un member cualquiera",
        },
    )
    assert resp.status_code == 403


def test_h4_scans_registra_quien_lo_disparo(client, monkeypatch, _sin_herramientas_reales):
    """`scans.user_id` -- la otra mitad de la trazabilidad de autorización."""
    import agents.escaneo as escaneo_module

    class _ResultadoFalso:
        findings: list = []

    monkeypatch.setattr(escaneo_module, "run_nuclei", lambda *_a, **_k: _ResultadoFalso())
    monkeypatch.setattr(escaneo_module, "run_zap_baseline", lambda *_a, **_k: _ResultadoFalso())

    token = _registrar_tenant(client, "Pyme H4 F", "h4f@bypass.test")
    _registrar_asset_verificado(client, token, "trazable-h4.test")
    user_id = client.get("/me", headers=_headers(token)).json()["user_id"]

    resp = client.post(
        "/scan",
        headers=_headers(token),
        json={"target": "trazable-h4.test", "autorizacion_firmada": True},
    )
    assert resp.status_code == 200

    conn = get_conn()
    try:
        filas = conn.execute(
            "SELECT user_id FROM scans WHERE target = ?", ("trazable-h4.test",)
        ).fetchall()
    finally:
        conn.close()
    assert filas
    assert all(f["user_id"] == user_id for f in filas)


def test_h4_reporte_ejecutivo_refleja_el_estado_real_de_autorizacion():
    """`tools/report_export.py` decía "solo escaneo pasivo" para TODO activo,
    porque la columna nunca se escribía. Ahora distingue los dos casos."""
    from tools.report_export import construir_markdown_ejecutivo

    md_sin = construir_markdown_ejecutivo(
        tenant_nombre="T",
        generado_en="2026-07-31",
        kpis={"porcentaje_dentro_sla": None, "mttr_por_severidad": []},
        assets=[{"id": "a1", "tipo": "dominio", "valor": "x.test", "verificado": 1, "autorizacion_activa": 0}],
        postura_por_activo={},
    )
    assert "sin autorización firmada para escaneo activo recurrente" in md_sin

    md_con = construir_markdown_ejecutivo(
        tenant_nombre="T",
        generado_en="2026-07-31",
        kpis={"porcentaje_dentro_sla": None, "mttr_por_severidad": []},
        assets=[
            {
                "id": "a1",
                "tipo": "dominio",
                "valor": "x.test",
                "verificado": 1,
                "autorizacion_activa": 1,
                "autorizado_por": "Ana Gerente",
            }
        ],
        postura_por_activo={},
    )
    assert "con autorización firmada para escaneo activo recurrente" in md_con
    assert "Ana Gerente" in md_con


def test_h4_el_scheduler_lanza_escaneo_activo_solo_tras_la_autorizacion(client, monkeypatch):
    """Cierra el ciclo completo de H-4: el endpoint nuevo es lo que de verdad
    enciende la rama activa de `run_scan_cycle_once`, que hasta ahora era
    inalcanzable por API."""
    import api.scheduler as scheduler_module

    token = _registrar_tenant(client, "Pyme H4 G", "h4g@bypass.test")
    asset_id = _registrar_asset_verificado(client, token, "ciclo-h4.test")

    caminos: list[str] = []
    monkeypatch.setattr(
        scheduler_module, "_ejecutar_ciclo_pasivo", lambda *_a: caminos.append("pasivo")
    )
    monkeypatch.setattr(
        scheduler_module, "_ejecutar_ciclo_activo", lambda *_a: caminos.append("activo")
    )
    monkeypatch.setattr(scheduler_module, "_get_graph", lambda: None)

    scheduler_module.run_scan_cycle_once()
    assert caminos == ["pasivo"]

    resp = client.post(
        f"/assets/{asset_id}/autorizacion-activa",
        headers=_headers(token),
        json={
            "activa": True,
            "autorizacion_doc_url": "https://drive.test/doc.pdf",
            "autorizado_por": "Ana",
        },
    )
    assert resp.status_code == 200

    caminos.clear()
    scheduler_module.run_scan_cycle_once()
    assert caminos == ["activo"]
