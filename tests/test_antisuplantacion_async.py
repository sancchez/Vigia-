"""`POST /antisuplantacion` asíncrono (202 + polling) — el arreglo del último
gap medido del producto de protección de marca.

MOTIVO MEDIDO (2026-08-01, corrida en vivo contra un dominio bancario real
con 45 lookalikes registrados): el endpoint era SÍNCRONO y tardó más de 15
minutos; el cliente HTTP cortó antes de que el servidor terminara. Detrás de
cualquier reverse proxy con timeout típico (30-100s en Render/Railway/nginx
por default) eso es un error para el cliente aunque el trabajo termine bien
del lado del servidor.

Estos tests protegen las tres propiedades que definen "asíncrono de verdad",
y NINGUNA se puede verificar mirando el código:

1. el POST vuelve rápido, sin esperar el pipeline (se bloquea el nodo con un
   `threading.Event` y se mide que el POST igual responde);
2. mientras el trabajo corre, el estado es consultable y dice `corriendo`;
3. al terminar queda `completado` con los hallazgos reales persistidos (y
   `error` con el motivo si el trabajo falla, en vez de quedarse colgado en
   `corriendo` para siempre).
"""

from __future__ import annotations

import threading
import time
import uuid

import api.main as main_module

from tests.test_antisuplantacion_endpoint import (
    _crear_asset,
    _mockear_nodo_y_probes,
    _registrar_tenant,
    _verificar_asset_mock,
    correr_antisuplantacion,
)


def _asset_verificado(client, monkeypatch, *, nombre: str, email: str, dominio: str) -> tuple[str, str]:
    token = _registrar_tenant(client, nombre, email)
    asset_id = _crear_asset(client, token, "dominio", dominio)
    _verificar_asset_mock(client, token, asset_id, monkeypatch)
    return token, asset_id


def test_post_devuelve_202_sin_esperar_el_pipeline(client, monkeypatch):
    """La propiedad central: el POST responde aunque el trabajo real siga
    corriendo. El nodo se bloquea hasta que el test lo libera, así que si el
    endpoint volviera a ser síncrono este test colgaría hasta el timeout."""
    _mockear_nodo_y_probes(monkeypatch)
    token, asset_id = _asset_verificado(
        client, monkeypatch, nombre="Pyme Async", email="async@antisup.test", dominio="asincrono.test"
    )

    liberar = threading.Event()
    entro_al_nodo = threading.Event()

    def _nodo_lento(_estado):
        entro_al_nodo.set()
        # Cota dura: si el test falla por otro motivo, el thread no queda vivo.
        liberar.wait(timeout=30)
        return {"antisuplantacion_findings": []}

    monkeypatch.setattr(main_module, "antisuplantacion_node", _nodo_lento)

    inicio = time.monotonic()
    resp = client.post(
        "/antisuplantacion",
        headers={"Authorization": f"Bearer {token}"},
        json={"asset_id": asset_id},
    )
    demora = time.monotonic() - inicio

    try:
        assert resp.status_code == 202, resp.text
        assert resp.json()["estado"] == "corriendo"
        assert uuid.UUID(resp.json()["scan_id"])
        # Generoso a propósito (el trabajo mockeado bloquea hasta 30s): lo que
        # se está midiendo es "no espera al pipeline", no un benchmark.
        assert demora < 5.0, f"el POST esperó al trabajo en background ({demora:.1f}s)"
        assert entro_al_nodo.wait(timeout=10), "el trabajo en background nunca arrancó"
    finally:
        liberar.set()


def test_estado_consultable_mientras_corre_y_luego_completado(client, monkeypatch):
    """`GET /antisuplantacion/runs/{id}` responde `corriendo` durante el
    trabajo y `completado` después — sin esa consulta el 202 sería inútil."""
    _mockear_nodo_y_probes(monkeypatch)
    token, asset_id = _asset_verificado(
        client, monkeypatch, nombre="Pyme Polling", email="polling@antisup.test", dominio="polling.test"
    )
    headers = {"Authorization": f"Bearer {token}"}

    liberar = threading.Event()
    entro_al_nodo = threading.Event()

    def _nodo_lento(_estado):
        entro_al_nodo.set()
        liberar.wait(timeout=30)
        return {
            "antisuplantacion_findings": [
                {
                    "señales_crudas": [
                        {
                            "tipo": "dominio_variante",
                            "fuzzer": "bitsquatting",
                            "dominio": "clon-polling.test",
                            "registrado": True,
                        }
                    ],
                    "analisis_claude": None,
                }
            ]
        }

    monkeypatch.setattr(main_module, "antisuplantacion_node", _nodo_lento)

    resp = client.post("/antisuplantacion", headers=headers, json={"asset_id": asset_id})
    assert resp.status_code == 202
    scan_id = resp.json()["scan_id"]

    try:
        assert entro_al_nodo.wait(timeout=10)
        en_curso = client.get(f"/antisuplantacion/runs/{scan_id}", headers=headers)
        assert en_curso.status_code == 200
        assert en_curso.json()["estado"] == "corriendo"
        assert en_curso.json()["target"] == "polling.test"
        # Y la vista genérica de scans ve exactamente el mismo estado.
        assert client.get(f"/scans/{scan_id}", headers=headers).json()["estado"] == "corriendo"
    finally:
        liberar.set()

    limite = time.monotonic() + 20
    while True:
        data = client.get(f"/antisuplantacion/runs/{scan_id}", headers=headers).json()
        if data["estado"] != "corriendo":
            break
        assert time.monotonic() < limite, "la corrida nunca terminó"
        time.sleep(0.05)

    assert data["estado"] == "completado"
    assert data["completed_at"]
    assert [d["endpoint"] for d in data["dominios"]] == ["clon-polling.test"]
    # El hallazgo quedó realmente persistido, no solo devuelto en la respuesta.
    listado = client.get("/antisuplantacion/dominios", headers=headers).json()
    assert [d["endpoint"] for d in listado] == ["clon-polling.test"]


def test_progreso_visible_durante_el_enriquecimiento(client, monkeypatch):
    """El checkpoint de progreso NO es cosmético: la etapa que domina los 15
    minutos es el enriquecimiento por dominio, así que tiene que reportarse
    dominio a dominio para que el frontend pueda mostrar algo distinto de un
    spinner mudo."""
    _mockear_nodo_y_probes(monkeypatch)
    token, asset_id = _asset_verificado(
        client, monkeypatch, nombre="Pyme Progreso", email="progreso@antisup.test", dominio="progreso.test"
    )
    headers = {"Authorization": f"Bearer {token}"}

    señales = [
        {
            "señales_crudas": [
                {"tipo": "dominio_variante", "fuzzer": "addition", "dominio": d, "registrado": True}
                for d in ("clon-uno.test", "clon-dos.test")
            ],
            "analisis_claude": None,
        }
    ]
    monkeypatch.setattr(
        main_module, "antisuplantacion_node", lambda _estado: {"antisuplantacion_findings": señales}
    )

    liberar_segundo = threading.Event()
    en_el_segundo = threading.Event()
    original = main_module.puntuar_infra_lookalike

    def _lento_en_el_segundo(dominio, timeout=10):
        if dominio == "clon-dos.test":
            en_el_segundo.set()
            liberar_segundo.wait(timeout=30)
        return original(dominio, timeout=timeout)

    monkeypatch.setattr(main_module, "puntuar_infra_lookalike", _lento_en_el_segundo)

    resp = client.post("/antisuplantacion", headers=headers, json={"asset_id": asset_id})
    assert resp.status_code == 202
    scan_id = resp.json()["scan_id"]

    try:
        assert en_el_segundo.wait(timeout=10)
        progreso = client.get(f"/antisuplantacion/runs/{scan_id}", headers=headers).json()["progreso"]
        assert progreso, "sin progreso el usuario no sabe si el trabajo avanza o se colgó"
        assert "2 de 2" in progreso, progreso
        assert "clon-dos.test" in progreso, progreso
    finally:
        liberar_segundo.set()

    limite = time.monotonic() + 20
    while client.get(f"/antisuplantacion/runs/{scan_id}", headers=headers).json()["estado"] == "corriendo":
        assert time.monotonic() < limite, "la corrida nunca terminó"
        time.sleep(0.05)


def test_fallo_en_background_deja_estado_error_con_motivo(client, monkeypatch):
    """Un trabajo que revienta no puede quedarse en 'corriendo' para siempre:
    el cliente estaría haciendo polling eternamente. Mismo contrato que
    `_correr_escaneo_activo_en_background`."""
    _mockear_nodo_y_probes(monkeypatch)
    token, asset_id = _asset_verificado(
        client, monkeypatch, nombre="Pyme Rota", email="rota@antisup.test", dominio="rota.test"
    )

    def _nodo_que_revienta(_estado):
        raise RuntimeError("dnstwist no está instalado")

    monkeypatch.setattr(main_module, "antisuplantacion_node", _nodo_que_revienta)

    data = correr_antisuplantacion(client, token, asset_id)
    assert data["estado"] == "error"
    assert "dnstwist no está instalado" in (data["progreso"] or "")


def test_corrida_de_otro_tenant_da_404(client, monkeypatch):
    """El endpoint de estado es tenant-scoped como todo el resto: un scan_id
    filtrado no le sirve a nadie de otro tenant."""
    _mockear_nodo_y_probes(monkeypatch)
    token_dueno, asset_id = _asset_verificado(
        client, monkeypatch, nombre="Dueno Runs", email="dueno@runs.test", dominio="dueno-runs.test"
    )
    data = correr_antisuplantacion(client, token_dueno, asset_id)

    token_ajeno = _registrar_tenant(client, "Ajeno Runs", "ajeno@runs.test")
    resp = client.get(
        f"/antisuplantacion/runs/{data['scan_id']}",
        headers={"Authorization": f"Bearer {token_ajeno}"},
    )
    assert resp.status_code == 404


def test_runs_inexistente_da_404(client):
    token = _registrar_tenant(client, "Sin Runs", "sinruns@runs.test")
    resp = client.get(
        f"/antisuplantacion/runs/{uuid.uuid4()}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404
