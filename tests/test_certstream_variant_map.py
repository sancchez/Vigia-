"""Fase 3b (openspec/changes/fase3b-vigilancia-continua-certstream) --
`_build_variant_map()` consume `watched_brands` activas.

Ver spec.md, dominio "Listener -- Consumo de watched_brands": una marca
activa entra al mapa de variantes tras el (re)build, y una marca cuya
generación de variantes falle se omite (log + continuar) sin tumbar el
resto del mapa -- mismo criterio que ya aplica el bucle de activos de
dominio.

Se usa `test_db` (fixture de `conftest.py`) directo, sin `TestClient`: solo
nos interesa la función pura `_build_variant_map()` contra una DB SQLite
temporal, con `generate_domain_variants` monkeypateado para no depender de
`dnstwist` real ni de tiempos de fuzzing.
"""

from __future__ import annotations

import uuid

import api.certstream_listener as listener_module
from db.connection import get_conn


def _crear_tenant(conn, tenant_id: str) -> None:
    conn.execute(
        "INSERT INTO tenants (id, slug, name) VALUES (?, ?, ?)",
        (tenant_id, tenant_id, f"Tenant {tenant_id}"),
    )


def _sembrar_marca(
    tenant_id: str,
    marca: str,
    is_active: bool = True,
    dominio_asociado: str | None = "miempresa.com",
) -> None:
    """`dominio_asociado` no vacío por default: desde el cierre del hueco de
    propiedad de marcas, `_build_variant_map` solo consume marcas ancladas a
    un dominio verificado del tenant (ver api/main.py::
    `_dominio_verificado_para_marca`). `dominio_asociado=None` siembra a
    propósito una fila LEGADA, anterior a esa migración."""
    conn = get_conn()
    try:
        if not conn.execute("SELECT 1 FROM tenants WHERE id = ?", (tenant_id,)).fetchone():
            _crear_tenant(conn, tenant_id)
        conn.execute(
            "INSERT INTO watched_brands (id, tenant_id, marca, dominio_asociado, is_active) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), tenant_id, marca, dominio_asociado, 1 if is_active else 0),
        )
        conn.commit()
    finally:
        conn.close()


def test_marca_normalizada_sin_punto_recibe_tld_default():
    assert listener_module._normalizar_marca_a_dominio("bancoacme") == "bancoacme.com"
    assert listener_module._normalizar_marca_a_dominio("BancoAcme") == "bancoacme.com"


def test_marca_con_punto_se_usa_tal_cual():
    assert listener_module._normalizar_marca_a_dominio("bancoacme.co") == "bancoacme.co"


def test_marca_activa_entra_al_mapa_tras_el_build(test_db, monkeypatch):
    tenant_id = "tenant-marca-1"
    _sembrar_marca(tenant_id, "acme")

    def fake_generate(domain: str):
        assert domain == "acme.com"
        return {"acmee.com", "acnne.com"}

    monkeypatch.setattr(listener_module, "generate_domain_variants", fake_generate)

    mapa = listener_module._build_variant_map()

    assert "acmee.com" in mapa
    assert "acnne.com" in mapa
    tenant_asociado, asset_id, dominio_base = mapa["acmee.com"]
    assert tenant_asociado == tenant_id
    assert asset_id is None  # sin activo de dominio detrás, ver design.md
    assert dominio_base == "acme.com"


def test_marca_inactiva_no_entra_al_mapa(test_db, monkeypatch):
    tenant_id = "tenant-marca-2"
    _sembrar_marca(tenant_id, "acme", is_active=False)

    def fake_generate(domain: str):
        return {"acmee.com"}

    monkeypatch.setattr(listener_module, "generate_domain_variants", fake_generate)

    mapa = listener_module._build_variant_map()
    assert "acmee.com" not in mapa


def test_marca_legada_sin_dominio_asociado_no_entra_al_mapa(test_db, monkeypatch):
    """Fila anterior a la migración de propiedad de marcas: se conserva en la
    tabla (nada se borra, el arranque no rompe) pero NO alimenta la
    vigilancia continua hasta que su tenant la re-registre anclada a un
    dominio verificado. Ver db/schema.sql y api/main.py::crear_watched_brand."""
    tenant_id = "tenant-marca-legada"
    _sembrar_marca(tenant_id, "acme", dominio_asociado=None)

    monkeypatch.setattr(
        listener_module, "generate_domain_variants", lambda _d: {"acmee.com"}
    )

    mapa = listener_module._build_variant_map()
    assert "acmee.com" not in mapa


def test_marca_invalida_no_rompe_el_mapa(test_db, monkeypatch):
    tenant_id = "tenant-marca-3"
    _sembrar_marca(tenant_id, "marcaRota")
    _sembrar_marca(tenant_id, "marcaBuena")

    def fake_generate(domain: str):
        if domain == "marcarota.com":
            raise RuntimeError("dnstwist explotó para esta marca")
        return {f"{domain}-variante.com"}

    monkeypatch.setattr(listener_module, "generate_domain_variants", fake_generate)

    mapa = listener_module._build_variant_map()

    # la marca rota se omite (log + continuar), la buena sigue en el mapa
    assert not any(v[2] == "marcarota.com" for v in mapa.values())
    assert "marcabuena.com-variante.com" in mapa
