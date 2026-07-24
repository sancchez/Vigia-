"""Servicio FastAPI del MVP — Fase 1 (sección 5 del plan maestro).

    "Envolver Nuclei + ZAP baseline en un servicio FastAPI simple. Un solo
    agente dispara el escaneo, Claude arma un reporte básico. Se prueba
    únicamente contra apps de laboratorio."

Esta capa HTTP es deliberadamente delgada: NO decide si el escaneo activo
puede ejecutarse. Esa decisión es 100% del nodo determinista
`gate_autorizacion` en `orchestrator/graph.py` (sección 8.1 del plan — "la
seguridad del sistema no depende de que el LLM se acuerde de la regla", y
tampoco depende de que esta capa HTTP se acuerde). Aquí solo se valida la
FORMA de los datos de entrada (Pydantic) y se invoca el grafo ya compilado.

AVISO LEGAL — léelo antes de apuntar este servicio a cualquier lado:
Este servicio NUNCA debe usarse contra dominios/IPs/apps de terceros sin
una autorización de pruebas de seguridad firmada por el dueño del sistema
(ver `legal/autorizacion-pruebas-seguridad.md` y sección 0 del plan
maestro). El acceso no autorizado a un sistema informático es delito en
Colombia bajo la Ley 1273 de 2009, sin importar la intención. Mientras no
exista ese documento firmado para un cliente real, las únicas pruebas
válidas son contra aplicaciones de laboratorio de código abierto pensadas
para practicar: OWASP Juice Shop (ver `eval/setup_juiceshop.md`) y DVWA.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from auth.jwt_auth import (
    UserContext,
    create_access_token,
    get_current_user,
    get_password_hash,
    require_role,
    verify_password,
)
from agents.antisuplantacion import node as antisuplantacion_node
from agents.cumplimiento import generar_reporte_cumplimiento
from agents.verificacion import verificar_un_hallazgo
from api.certstream_listener import start_certstream_listener, stop_certstream_listener
from api.scheduler import start_scheduler, stop_scheduler
from db.connection import dict_from_row, get_conn
from orchestrator.graph import compile_graph
from orchestrator.state import new_state
from tools._shared import ToolExecutionError, normalize_severity
from tools.antisuplantacion import capturar_screenshot, puntuar_infra_lookalike, registrable_domain
from tools.asset_verification import (
    METODOS_VALIDOS,
    asset_autorizado_para_escanear,
    es_target_exento_de_verificacion,
    generar_token as generar_token_verificacion,
    instrucciones_verificacion,
    verificar_asset,
)
from tools.report_export import markdown_to_docx_bytes, markdown_to_pdf_bytes
from tools.scan import run_nuclei
from tools.zap_api import run_checkpointed_active_scan, run_single_target_verification
from urllib.parse import urlparse

load_dotenv()

AVISO_LEGAL = (
    "Este servicio NO debe apuntarse contra sistemas de terceros sin una "
    "autorizacion de pruebas de seguridad firmada por el dueno (ver "
    "legal/autorizacion-pruebas-seguridad.md). Las unicas pruebas de "
    "referencia habilitadas hoy son aplicaciones de laboratorio: OWASP "
    "Juice Shop (eval/setup_juiceshop.md) y DVWA. Si 'autorizacion_firmada' "
    "no es true, el grafo bloquea el Escaneo Activo de forma determinista "
    "(ver orchestrator/graph.py::gate_autorizacion) — esta capa HTTP no "
    "aplica ni duplica esa regla, solo la reporta."
)

_compiled_graph: Any = None


def _get_graph() -> Any:
    """Compila el grafo una sola vez (module-level cache) y lo reutiliza.

    Compilar por request sería correcto pero desperdicia trabajo — el grafo
    no tiene estado propio entre invocaciones (`PipelineState` viaja como
    argumento de `.invoke()`), así que un solo `CompiledStateGraph` es
    seguro de reutilizar entre requests concurrentes.
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = compile_graph()
    return _compiled_graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Compila el grafo al arrancar (falla rápido si algo del ensamblaje del
    # StateGraph está roto, en vez de fallar en el primer /scan).
    _get_graph()
    start_scheduler()
    # Item 5 (HANDOFF.md): vigilancia continua real vía CertStream. Mismo
    # patrón que start_scheduler() — daemon thread dentro de este proceso.
    # Nunca lanza: se apaga solo si falta el paquete `certstream` o el feed
    # configurado no responde (ver api/certstream_listener.py).
    start_certstream_listener()
    yield
    stop_certstream_listener()
    stop_scheduler()


app = FastAPI(
    title="Ciberseguridad IA — API MVP (Fase 1)",
    description=(
        "Servicio FastAPI que envuelve el pipeline LangGraph de escaneo de "
        "seguridad (Recon -> gate autorizacion -> Escaneo -> Verificacion -> "
        "Priorizacion -> Remediacion -> Reporteria). "
        + AVISO_LEGAL
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # Dev: el frontend (Vite) corre en otro puerto. Restringir a orígenes
    # reales (dominio del panel en producción) antes de desplegar.
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScopeIn(BaseModel):
    """Espejo de `orchestrator.state.Scope` — solo forma, sin reglas de negocio."""

    dominios: list[str] = Field(default_factory=list)
    apps: list[str] = Field(default_factory=list)
    ips: list[str] = Field(default_factory=list)
    notas: str = ""
    codigo_paths: list[str] = Field(
        default_factory=list,
        description="Rutas locales de código fuente para SAST (Semgrep). No son URLs.",
    )
    imagenes: list[str] = Field(
        default_factory=list,
        description="Referencias de imagen de contenedor para SCA/CVE (Trivy+Grype). No son URLs.",
    )


class ScanRequest(BaseModel):
    """Payload de `POST /scan`.

    IMPORTANTE: `autorizacion_firmada=False` es un valor VÁLIDO y aceptado
    por este endpoint — la petición no se rechaza en la capa HTTP. Es el
    grafo (nodo `gate_autorizacion`, código determinista) el que decide
    bloquear el Escaneo Activo y desviar el flujo a `bloqueo_autorizacion`.
    Duplicar esa validación aquí violaría la sección 8.1 del plan maestro
    (una sola fuente de verdad para la puerta de seguridad).
    """

    target: str = Field(..., description="Dominio/marca principal, ej. 'miempresa.com'")
    scope: ScopeIn = Field(default_factory=ScopeIn)
    autorizacion_firmada: bool = Field(
        default=False,
        description=(
            "Debe ser true y estar respaldado por un documento firmado "
            "(legal/autorizacion-pruebas-seguridad.md) para que el grafo "
            "permita Escaneo Activo. false es aceptado igual: el grafo "
            "responde con un reporte explicando el bloqueo."
        ),
    )
    contexto_negocio: str = Field(
        default="", description="Qué hace la empresa / qué sistemas son críticos (usa Priorización)"
    )
    antisuplantacion_habilitado: bool = Field(
        default=False, description="Activa la rama opcional Anti-Suplantación (sección 4 del plan)"
    )


class ScanResponse(BaseModel):
    """Proyección del `PipelineState` final tras `.invoke()` — sin reglas propias."""

    target: str
    autorizacion_firmada: bool
    autorizacion_bloqueo_motivo: Optional[str] = None
    recon_findings: list[dict] = Field(default_factory=list)
    scan_findings: list[dict] = Field(default_factory=list)
    verified_findings: list[dict] = Field(default_factory=list)
    prioritized_findings: list[dict] = Field(default_factory=list)
    remediations: list[dict] = Field(default_factory=list)
    antisuplantacion_findings: list[dict] = Field(default_factory=list)
    reporte_final: Optional[str] = None
    trace_log: list[dict] = Field(default_factory=list)
    aviso_legal: str = AVISO_LEGAL


class RegisterRequest(BaseModel):
    """Registro de un usuario nuevo — dos caminos posibles.

    Sin `invite_token`: comportamiento original, crea un tenant nuevo (una
    pyme cliente) con este usuario como su primer `owner`. `nombre_negocio`
    es obligatorio en este camino (se valida a mano abajo, no vía Pydantic,
    porque el campo también debe aceptarse vacío en el camino de invitación).

    Con `invite_token`: NO crea un tenant nuevo — el usuario se agrega al
    tenant de la invitación (`invitations.tenant_id`) con el `role` que la
    invitación especificó (`admin` o `member`). Item transversal de
    HANDOFF.md ("invitar más usuarios al mismo tenant").
    """

    nombre_negocio: str = Field(default="", description="Nombre del negocio, ej. 'Mi Pyme S.A.S.' (requerido si no hay invite_token)")
    email: str
    password: str = Field(..., min_length=8)
    invite_token: Optional[str] = Field(
        default=None, description="Token de una invitación pendiente (ver POST /tenant/invitations)"
    )


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    user_id: str
    tenant_id: str
    tenant_nombre: str
    email: str
    role: str
    plan: str


class AssetIn(BaseModel):
    tipo: str = Field(..., description="'dominio', 'app' o 'ip'")
    valor: str = Field(..., description="ej. 'miempresa.com'")
    notas: str = ""


class AssetOut(BaseModel):
    """Incluye el estado real de verificación de propiedad (ver `tools/asset_verification.py`).

    `instrucciones_verificacion` viaja siempre que haga falta que el tenant
    actúe (asset tipo dominio/app, ni exento ni ya verificado) -- pensado
    para que el frontend no tenga que reconstruir las URLs/nombres de
    registro por su cuenta, y para que quien llame a la API directo (curl,
    Postman) tenga todo lo que necesita en la misma respuesta que crea el
    asset.
    """

    id: str
    tipo: str
    valor: str
    notas: str
    is_active: bool
    created_at: str
    verificado: bool
    verification_method: Optional[str] = None
    verified_at: Optional[str] = None
    exento_de_verificacion: bool
    instrucciones_verificacion: Optional[dict] = None


class VerificarAssetIn(BaseModel):
    metodo: str = Field(..., description="'dns_txt' o 'http_file' -- ver GET /assets para las instrucciones exactas")


class VerificarAssetOut(BaseModel):
    verificado: bool
    detalle: str
    verification_method: Optional[str] = None
    verified_at: Optional[str] = None


class ScanHistoryItem(BaseModel):
    id: str
    target: str
    estado: str
    autorizacion_firmada: bool
    total_hallazgos: int
    created_at: str
    completed_at: Optional[str] = None


class FindingOut(BaseModel):
    """Contrato exacto fijado en design.md de `fase2a-ciclo-vida-hallazgo` --
    `fase2b-tablero-remediacion` (frontend, en paralelo) construye contra
    estos 6 campos nuevos sin ver código; no cambiar sin nueva ronda de diseño.
    """

    id: str
    scan_id: str
    tipo: str
    severidad: str
    endpoint: str
    confirmado: bool
    created_at: str
    estado: str
    owner_user_id: Optional[str] = None
    owner_email: Optional[str] = None
    sla_due_at: Optional[str] = None
    primera_vez_visto: Optional[str] = None
    ultima_vez_visto: Optional[str] = None


def _slugify(nombre: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", nombre.lower()).strip("-")
    return slug or "tenant"


def _unique_slug(conn, base_slug: str) -> str:
    slug = base_slug
    n = 1
    while conn.execute("SELECT 1 FROM tenants WHERE slug = ?", (slug,)).fetchone():
        n += 1
        slug = f"{base_slug}-{n}"
    return slug


@app.post("/auth/register", response_model=TokenResponse)
def register(payload: RegisterRequest) -> TokenResponse:
    """Crea un usuario nuevo: tenant propio (owner) o unido a uno existente (invitación).

    `users.email` es UNIQUE global en el schema (una cuenta = un tenant), así
    que ambos caminos comparten la misma verificación de email duplicado
    antes de decidir cuál de los dos ejecutar.
    """
    conn = get_conn()
    try:
        existing = conn.execute("SELECT 1 FROM users WHERE email = ?", (payload.email,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese email")

        user_id = str(uuid.uuid4())

        if payload.invite_token:
            invitation = conn.execute(
                "SELECT * FROM invitations WHERE token = ?", (payload.invite_token,)
            ).fetchone()
            if invitation is None:
                raise HTTPException(status_code=400, detail="Invitación no encontrada")
            if invitation["estado"] != "pendiente":
                raise HTTPException(status_code=400, detail="Esta invitación ya fue usada o revocada")
            if invitation["expires_at"] and datetime.fromisoformat(invitation["expires_at"]) < datetime.now(timezone.utc):
                raise HTTPException(status_code=400, detail="Esta invitación expiró")
            if invitation["email"].strip().lower() != payload.email.strip().lower():
                raise HTTPException(
                    status_code=400, detail="El email no coincide con el de la invitación"
                )

            tenant_id = invitation["tenant_id"]
            role = invitation["role"]
            tenant_row = conn.execute("SELECT plan FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
            plan = tenant_row["plan"] if tenant_row else "trial"

            conn.execute(
                "INSERT INTO users (id, tenant_id, email, hashed_password, role) VALUES (?, ?, ?, ?, ?)",
                (user_id, tenant_id, payload.email, get_password_hash(payload.password), role),
            )
            conn.execute(
                "UPDATE invitations SET estado = 'aceptada', accepted_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), invitation["id"]),
            )
            conn.commit()
        else:
            if not payload.nombre_negocio.strip():
                raise HTTPException(
                    status_code=422,
                    detail="nombre_negocio es obligatorio para crear un negocio nuevo (o usa invite_token)",
                )
            tenant_id = str(uuid.uuid4())
            slug = _unique_slug(conn, _slugify(payload.nombre_negocio))
            role = "owner"
            plan = "trial"

            conn.execute(
                "INSERT INTO tenants (id, slug, name, plan) VALUES (?, ?, ?, 'trial')",
                (tenant_id, slug, payload.nombre_negocio),
            )
            conn.execute(
                "INSERT INTO users (id, tenant_id, email, hashed_password, role) VALUES (?, ?, ?, ?, 'owner')",
                (user_id, tenant_id, payload.email, get_password_hash(payload.password)),
            )
            conn.execute(
                "INSERT INTO subscriptions (tenant_id, plan, estado) VALUES (?, 'trial', 'trial')",
                (tenant_id,),
            )
            conn.commit()
    finally:
        conn.close()

    token = create_access_token(
        {"sub": user_id, "tenant_id": tenant_id, "email": payload.email, "role": role, "plan": plan}
    )
    return TokenResponse(access_token=token)


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT u.id AS user_id, u.tenant_id, u.hashed_password, u.role, t.plan
            FROM users u JOIN tenants t ON t.id = u.tenant_id
            WHERE u.email = ?
            """,
            (payload.email,),
        ).fetchone()
    finally:
        conn.close()

    if row is None or not verify_password(payload.password, row["hashed_password"]):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

    token = create_access_token(
        {
            "sub": row["user_id"],
            "tenant_id": row["tenant_id"],
            "email": payload.email,
            "role": row["role"],
            "plan": row["plan"],
        }
    )
    return TokenResponse(access_token=token)


@app.get("/me", response_model=MeResponse)
def me(user: UserContext = Depends(get_current_user)) -> MeResponse:
    conn = get_conn()
    try:
        row = conn.execute("SELECT name FROM tenants WHERE id = ?", (user.tenant_id,)).fetchone()
    finally:
        conn.close()
    return MeResponse(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        tenant_nombre=row["name"] if row else "",
        email=user.email,
        role=user.role,
        plan=user.plan,
    )


def _asset_out_de_row(d: dict) -> AssetOut:
    """Proyecta una fila cruda de `assets` a `AssetOut`, agregando los campos calculados
    (exención, instrucciones) que no viven en la DB -- ver `tools/asset_verification.py`."""
    verificado = bool(d["verificado"])
    exento = es_target_exento_de_verificacion(d["tipo"], d["valor"])
    instrucciones = None
    if not verificado and not exento:
        instrucciones = instrucciones_verificacion(d["tipo"], d["valor"], d.get("verification_token") or "")
    return AssetOut(
        **{
            **d,
            "is_active": bool(d["is_active"]),
            "verificado": verificado,
            "exento_de_verificacion": exento,
            "instrucciones_verificacion": instrucciones,
        }
    )


@app.post("/assets", response_model=AssetOut)
def crear_asset(payload: AssetIn, user: UserContext = Depends(get_current_user)) -> AssetOut:
    if payload.tipo not in ("dominio", "app", "ip"):
        raise HTTPException(status_code=422, detail="tipo debe ser 'dominio', 'app' o 'ip'")
    asset_id = str(uuid.uuid4())
    # Token generado siempre, incluso para assets exentos (localhost/IP
    # privada) -- barato, y deja la puerta abierta a que un asset hoy exento
    # (ej. 'localhost') se reapunte manualmente a un dominio público real en
    # el futuro sin tener que regenerar nada. Ver tools/asset_verification.py.
    token = generar_token_verificacion()
    conn = get_conn()
    try:
        try:
            conn.execute(
                """
                INSERT INTO assets (id, tenant_id, tipo, valor, notas, verification_token)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (asset_id, user.tenant_id, payload.tipo, payload.valor, payload.notas, token),
            )
            conn.commit()
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"Ese activo ya está registrado: {exc}")
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    finally:
        conn.close()
    return _asset_out_de_row(dict_from_row(row))


@app.get("/assets", response_model=list[AssetOut])
def listar_assets(user: UserContext = Depends(get_current_user)) -> list[AssetOut]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM assets WHERE tenant_id = ? ORDER BY created_at DESC", (user.tenant_id,)
        ).fetchall()
    finally:
        conn.close()
    return [_asset_out_de_row(dict_from_row(r)) for r in rows]


@app.post("/assets/{asset_id}/verify", response_model=VerificarAssetOut)
def verificar_asset_endpoint(
    asset_id: str, payload: VerificarAssetIn, user: UserContext = Depends(get_current_user)
) -> VerificarAssetOut:
    """Ejecuta de verdad la comprobación DNS/HTTP pedida y marca el asset verificado si pasa.

    Idempotente y seguro de reintentar: si el asset ya está verificado, lo
    vuelve a comprobar igual (no hay razón para bloquear una re-verificación
    -- ej. el tenant quiere confirmar que el TXT sigue presente tras rotar
    su proveedor de DNS) y actualiza `verified_at`/`verification_method` con
    el resultado más reciente.
    """
    if payload.metodo not in METODOS_VALIDOS:
        raise HTTPException(
            status_code=422,
            detail=f"metodo debe ser uno de {METODOS_VALIDOS}, recibido: {payload.metodo!r}",
        )
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM assets WHERE id = ? AND tenant_id = ?", (asset_id, user.tenant_id)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Activo no encontrado para este tenant.")
        asset = dict_from_row(row)

        if asset["tipo"] == "ip":
            raise HTTPException(
                status_code=422,
                detail="La verificación de propiedad no aplica a assets tipo 'ip' (no hay dominio del que colgar un TXT o un archivo).",
            )
        if es_target_exento_de_verificacion(asset["tipo"], asset["valor"]):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{asset['valor']}' es localhost/IP privada -- ya está exento de verificación "
                    "y puede escanearse sin este paso (ver POST /assets)."
                ),
            )

        token = asset.get("verification_token")
        if not token:
            # Asset creado antes de esta migración (o por algún camino que
            # no pasó por crear_asset) -- se genera aquí en vez de fallar,
            # mismo espíritu de degradar con gracia que el resto del proyecto.
            token = generar_token_verificacion()
            conn.execute(
                "UPDATE assets SET verification_token = ? WHERE id = ?", (token, asset_id)
            )
            conn.commit()

        ok, detalle = verificar_asset(asset["tipo"], asset["valor"], token, payload.metodo)
        now = datetime.now(timezone.utc).isoformat()
        if ok:
            conn.execute(
                "UPDATE assets SET verificado = 1, verified_at = ?, verification_method = ? WHERE id = ?",
                (now, payload.metodo, asset_id),
            )
            conn.commit()
        return VerificarAssetOut(
            verificado=ok,
            detalle=detalle,
            verification_method=payload.metodo if ok else None,
            verified_at=now if ok else None,
        )
    finally:
        conn.close()


@app.get("/scans", response_model=list[ScanHistoryItem])
def listar_scans(user: UserContext = Depends(get_current_user)) -> list[ScanHistoryItem]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT s.id, s.target, s.estado, s.autorizacion_firmada, s.created_at, s.completed_at,
                   (SELECT COUNT(*) FROM findings f WHERE f.scan_id = s.id) AS total_hallazgos
            FROM scans s WHERE s.tenant_id = ? ORDER BY s.created_at DESC
            """,
            (user.tenant_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        ScanHistoryItem(
            id=r["id"],
            target=r["target"],
            estado=r["estado"],
            autorizacion_firmada=bool(r["autorizacion_firmada"]),
            total_hallazgos=r["total_hallazgos"],
            created_at=r["created_at"],
            completed_at=r["completed_at"],
        )
        for r in rows
    ]


def _finding_out_de_row(d: dict) -> FindingOut:
    """Proyecta una fila de `findings` (opcionalmente con `owner_email` ya
    resuelto vía LEFT JOIN a `users`) a `FindingOut` -- centraliza el mapeo
    de los 6 campos de ciclo de vida usados por `listar_findings`,
    `detalle_scan`, y `PATCH /findings/{id}` (ver design.md, contrato fijo)."""
    return FindingOut(
        id=d["id"],
        scan_id=d["scan_id"],
        tipo=d["tipo"],
        severidad=d["severidad"],
        endpoint=d["endpoint"],
        confirmado=bool(d["confirmado"]),
        created_at=d["created_at"],
        estado=d["estado"],
        owner_user_id=d.get("owner_user_id"),
        owner_email=d.get("owner_email"),
        sla_due_at=d.get("sla_due_at"),
        primera_vez_visto=d.get("primera_vez_visto"),
        ultima_vez_visto=d.get("ultima_vez_visto"),
    )


@app.get("/findings", response_model=list[FindingOut])
def listar_findings(user: UserContext = Depends(get_current_user)) -> list[FindingOut]:
    """Todos los hallazgos del tenant, más recientes primero — usado por el panel para el resumen de riesgo."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT f.*, u.email AS owner_email
            FROM findings f LEFT JOIN users u ON u.id = f.owner_user_id
            WHERE f.tenant_id = ? ORDER BY f.created_at DESC
            """,
            (user.tenant_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_finding_out_de_row(dict_from_row(r)) for r in rows]


def _texto_desde_lista_o_string(valor: Any) -> str:
    """Normaliza un campo que, según la herramienta, puede venir como un
    string simple, una lista de strings, o ausente -- a un único string
    legible (join con "; "). Ej.: `info.reference` de nuclei y
    `metadata.references`/`metadata.cwe` de semgrep son listas; `reference`
    de ZAP y `cweid` son strings simples."""
    if valor is None:
        return ""
    if isinstance(valor, str):
        return valor
    if isinstance(valor, (list, tuple)):
        return "; ".join(str(v) for v in valor if v)
    return str(valor)


def _detalle_desde_raw_json(raw_json: str | None) -> dict:
    """Deriva los campos curados de `FindingDetailOut` a partir de
    `findings.raw_json` -- cero columna SQL nueva (ver design.md, Nota de
    DB: `estado_verificacion` como columna dedicada es responsabilidad de
    fase0-fundaciones-datos, corriendo en paralelo).

    Tolerante por diseño: `raw_json` ausente/vacío, JSON corrupto, o de un
    finding insertado antes de esta fase (sin las claves nuevas) siempre
    devuelve 200 con defaults vacíos -- nunca 500 (ver
    design.md::"Decision: parser tolerante").

    Mapeo de `descripcion`/`solucion`/`referencia`/`cwe` POR HERRAMIENTA --
    shape real confirmado leyendo `tools/scan.py` y `tools/_shared.py` en
    esta sesión de `sdd-apply` (ver design.md, sección "Open Questions"
    actualizada, que documenta el nivel de confianza exacto de cada mapeo:
    el de ZAP fue confirmado en una sesión anterior contra datos reales
    capturados; los de nuclei/semgrep/trivy/grype se basan en el schema
    JSON documentado y estable de cada herramienta, no en una recaptura
    en vivo de esta sesión puntual -- riesgo residual bajo, todo con
    `.get(...)` tolerante).

    Dispatch por `data["herramienta"]` (presente para hallazgos de
    `POST /scan`, ver `agents/escaneo.py`: `{"objetivo","herramienta","raw"}`
    más los campos de verificación al mismo nivel). Cuando `herramienta`
    está ausente -- alertas planas de escaneo activo
    (`tools/zap_api.py::get_alerts()`, sin wrapper) o findings legados/de
    otro origen (ej. `antisuplantacion_findings`) -- se usa el shape plano
    de ZAP como fallback seguro: si las claves `description`/`solution`/
    `reference`/`cweid` no existen, simplemente no aparecen (`.get()`
    devuelve default), no se inventa nada.
    """
    defaults = {
        "descripcion": "",
        "solucion": "",
        "referencia": "",
        "cwe": None,
        "estado_verificacion": None,
        "cves_detectados": [],
        "evidencia_exploitdb": [],
    }
    if not raw_json:
        return defaults
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return defaults
    if not isinstance(data, dict):
        return defaults

    resultado = dict(defaults)
    resultado["estado_verificacion"] = data.get("estado_verificacion")
    resultado["cves_detectados"] = data.get("cves_detectados") or []
    resultado["evidencia_exploitdb"] = data.get("evidencia_exploitdb") or []

    herramienta = str(data.get("herramienta") or "").strip().lower()
    raw = data.get("raw") if isinstance(data.get("raw"), dict) else None

    if herramienta == "nuclei":
        raw = raw or {}
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        resultado["descripcion"] = info.get("description") or ""
        resultado["referencia"] = _texto_desde_lista_o_string(info.get("reference"))
        clasificacion = info.get("classification") if isinstance(info.get("classification"), dict) else {}
        resultado["cwe"] = _texto_desde_lista_o_string(clasificacion.get("cwe-id")) or None
        # Nuclei no tiene un campo de "solución" dedicado en su schema JSONL
        # -- la remediación (si la hay) viaja dentro de `description`, no en
        # una clave propia. Se deja vacío a propósito, no inventado.

    elif herramienta == "semgrep":
        raw = raw or {}
        extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
        resultado["descripcion"] = extra.get("message") or ""
        metadata = extra.get("metadata") if isinstance(extra.get("metadata"), dict) else {}
        resultado["referencia"] = _texto_desde_lista_o_string(metadata.get("references"))
        resultado["cwe"] = _texto_desde_lista_o_string(metadata.get("cwe")) or None
        fix = metadata.get("fix") if isinstance(metadata.get("fix"), str) else None
        resultado["solucion"] = fix or ""

    elif herramienta == "trivy":
        raw = raw or {}
        resultado["descripcion"] = raw.get("Description") or raw.get("Title") or ""
        resultado["referencia"] = _texto_desde_lista_o_string(raw.get("References"))
        resultado["cwe"] = _texto_desde_lista_o_string(raw.get("CweIDs")) or None
        fixed_version = raw.get("FixedVersion")
        resultado["solucion"] = f"Actualizar a la versión corregida: {fixed_version}" if fixed_version else ""

    elif herramienta == "grype":
        raw = raw or {}
        vuln = raw.get("vulnerability") if isinstance(raw.get("vulnerability"), dict) else {}
        resultado["descripcion"] = vuln.get("description") or ""
        resultado["referencia"] = _texto_desde_lista_o_string(vuln.get("urls"))
        fix = vuln.get("fix") if isinstance(vuln.get("fix"), dict) else {}
        versiones = fix.get("versions") or []
        resultado["solucion"] = f"Actualizar a: {', '.join(versiones)}" if versiones else ""
        # Grype no expone CWE en el schema de `matches[].vulnerability` (a
        # diferencia de Trivy) -- queda None a propósito, no inventado.

    else:
        # ZAP (`zap-baseline`/`zap-full-scan` con wrapper "herramienta", y
        # alertas planas de escaneo activo checkpointed sin wrapper) comparten
        # el mismo shape confirmado: description/solution/reference/cweid a
        # nivel superior del propio hallazgo (design.md, decisión confirmada
        # antes de esta sesión). Este mismo bloque es también el fallback
        # seguro para "herramienta" ausente/desconocida (antisuplantacion,
        # datos legados): esas claves simplemente no existen ahí y quedan en
        # default vacío, sin error.
        raw_plano = raw if raw is not None else data
        resultado["descripcion"] = raw_plano.get("description") or ""
        resultado["solucion"] = raw_plano.get("solution") or ""
        resultado["referencia"] = _texto_desde_lista_o_string(raw_plano.get("reference"))
        cweid = raw_plano.get("cweid")
        resultado["cwe"] = str(cweid) if cweid not in (None, "") else None

    return resultado


class FindingDetailOut(BaseModel):
    id: str
    scan_id: str
    tipo: str
    severidad: str
    endpoint: str
    confirmado: bool
    created_at: str
    descripcion: str = ""
    solucion: str = ""
    referencia: str = ""
    cwe: Optional[str] = None
    estado_verificacion: Optional[str] = None
    cves_detectados: list[str] = Field(default_factory=list)
    evidencia_exploitdb: list[dict] = Field(default_factory=list)
    # Mismos 6 campos de ciclo de vida que `FindingOut` (ver design.md,
    # fase2a-ciclo-vida-hallazgo) -- mismos nombres/tipos, contrato fijo.
    estado: str = "descubierto"
    owner_user_id: Optional[str] = None
    owner_email: Optional[str] = None
    sla_due_at: Optional[str] = None
    primera_vez_visto: Optional[str] = None
    ultima_vez_visto: Optional[str] = None


@app.get("/findings/{finding_id}", response_model=FindingDetailOut)
def detalle_finding(finding_id: str, user: UserContext = Depends(get_current_user)) -> FindingDetailOut:
    """Detalle curado de un hallazgo -- descripción/solución/CWE nativos de
    la herramienta más los campos que ya calcula `agents/verificacion.py`
    (`estado_verificacion`, `cves_detectados`, `evidencia_exploitdb`), todo
    derivado de `findings.raw_json` (sin columna SQL nueva, ver design.md).

    Mismo scoping por tenant que `GET /scans/{scan_id}`: si el hallazgo no
    existe o pertenece a otro tenant, 404 (no 403) -- no confirmar la
    existencia del recurso a quien no es su dueño.

    NO devuelve `raw_json` crudo como campo de nivel superior del
    contrato HTTP -- solo los campos curados de `FindingDetailOut`.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT f.*, u.email AS owner_email
            FROM findings f LEFT JOIN users u ON u.id = f.owner_user_id
            WHERE f.id = ? AND f.tenant_id = ?
            """,
            (finding_id, user.tenant_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Hallazgo no encontrado")

    detalle = _detalle_desde_raw_json(row["raw_json"])
    return FindingDetailOut(
        id=row["id"],
        scan_id=row["scan_id"],
        tipo=row["tipo"],
        severidad=row["severidad"],
        endpoint=row["endpoint"],
        confirmado=bool(row["confirmado"]),
        created_at=row["created_at"],
        estado=row["estado"],
        owner_user_id=row["owner_user_id"],
        owner_email=row["owner_email"],
        sla_due_at=row["sla_due_at"],
        primera_vez_visto=row["primera_vez_visto"],
        ultima_vez_visto=row["ultima_vez_visto"],
        **detalle,
    )


class FindingPatchIn(BaseModel):
    """Payload de `PATCH /findings/{id}` -- ver design.md, contrato fijo para
    `fase2b-tablero-remediacion`. `estado` debe ser uno de los 8 valores del
    enum de `findings.estado`; `nota` es obligatoria (no vacía) cuando se
    envía `estado` -- se valida en el endpoint, no aquí, porque el 409 de
    transición inválida debe evaluarse ANTES que la falta de nota (ver
    spec.md, escenario "Transición inválida": pide 409 con un body que
    tampoco trae `nota`)."""

    estado: Optional[str] = None
    owner_user_id: Optional[str] = None
    nota: Optional[str] = Field(default=None, min_length=1)


@app.patch("/findings/{finding_id}", response_model=FindingOut)
def actualizar_finding(
    finding_id: str, payload: FindingPatchIn, user: UserContext = Depends(get_current_user)
) -> FindingOut:
    """Ciclo de vida del hallazgo (Fase 2a): transición de `estado` validada
    contra `TRANSICIONES_VALIDAS`, reasignación de `owner_user_id` (debe
    pertenecer al mismo tenant), y auditoría obligatoria -- cada cambio
    aplicado registra una fila nueva en `finding_transiciones` con timestamp
    real (`created_at` por DEFAULT CURRENT_TIMESTAMP), no aproximado --
    `fase2c-verificacion-cierre-y-kpis` (futura) calculará MTTR a partir de
    esas filas.

    Mismo scoping por tenant y mismo criterio 404-no-403 que
    `GET /findings/{id}` -- ver ese docstring: no se confirma la existencia
    del recurso a quien no es su dueño.
    """
    if payload.estado is None and payload.owner_user_id is None:
        raise HTTPException(
            status_code=422,
            detail="Debe enviarse 'estado' y/o 'owner_user_id'.",
        )

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM findings WHERE id = ? AND tenant_id = ?", (finding_id, user.tenant_id)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Hallazgo no encontrado")
        finding = dict_from_row(row)
        estado_actual = finding["estado"]

        estado_nuevo: Optional[str] = None
        if payload.estado is not None:
            if payload.estado not in TRANSICIONES_VALIDAS:
                raise HTTPException(
                    status_code=422, detail=f"'{payload.estado}' no es un estado válido."
                )
            # Chequeo de la máquina de estados ANTES que el de nota
            # obligatoria a propósito -- ver spec.md, escenario "Transición
            # inválida": un salto no permitido (ej. descubierto->cerrado)
            # debe responder 409 aunque tampoco traiga `nota`.
            if payload.estado not in TRANSICIONES_VALIDAS.get(estado_actual, set()):
                raise HTTPException(
                    status_code=409,
                    detail=f"Transición inválida: '{estado_actual}' -> '{payload.estado}'.",
                )
            if not payload.nota:
                raise HTTPException(
                    status_code=422,
                    detail="'nota' es obligatoria cuando se envía 'estado'.",
                )
            estado_nuevo = payload.estado

        owner_nuevo: Optional[str] = None
        if payload.owner_user_id is not None:
            pertenece_al_tenant = conn.execute(
                "SELECT 1 FROM users WHERE id = ? AND tenant_id = ?",
                (payload.owner_user_id, user.tenant_id),
            ).fetchone()
            if pertenece_al_tenant is None:
                raise HTTPException(
                    status_code=422,
                    detail="owner_user_id no pertenece a este tenant.",
                )
            owner_nuevo = payload.owner_user_id

        set_clauses: list[str] = []
        params: list[Any] = []
        if estado_nuevo is not None:
            set_clauses.append("estado = ?")
            params.append(estado_nuevo)
        if owner_nuevo is not None:
            set_clauses.append("owner_user_id = ?")
            params.append(owner_nuevo)
        if set_clauses:
            params.append(finding_id)
            conn.execute(f"UPDATE findings SET {', '.join(set_clauses)} WHERE id = ?", params)

        conn.execute(
            """
            INSERT INTO finding_transiciones
                (id, finding_id, tenant_id, estado_anterior, estado_nuevo,
                 owner_anterior, owner_nuevo, nota, changed_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                finding_id,
                user.tenant_id,
                estado_actual if estado_nuevo is not None else None,
                estado_nuevo,
                finding["owner_user_id"] if owner_nuevo is not None else None,
                owner_nuevo,
                payload.nota or "",
                user.user_id,
            ),
        )
        conn.commit()

        row_actualizado = conn.execute(
            """
            SELECT f.*, u.email AS owner_email
            FROM findings f LEFT JOIN users u ON u.id = f.owner_user_id
            WHERE f.id = ?
            """,
            (finding_id,),
        ).fetchone()
    finally:
        conn.close()

    return _finding_out_de_row(dict_from_row(row_actualizado))


# ---------------------------------------------------------------------------
# Verificación de cierre + KPIs de remediación
# (openspec/changes/fase2c-verificacion-cierre-y-kpis). Ver design.md para
# el flujo completo (Data Flow) y las decisiones de arquitectura.
# ---------------------------------------------------------------------------


class VerificarCierreOut(BaseModel):
    finding_id: str
    estado: str
    reaparece: bool
    detalle: str


def _herramienta_de_finding(raw_json: str | None) -> str:
    """Deriva la herramienta de origen de un finding para el dispatch de
    `POST /findings/{id}/verificar-cierre` -- mismo criterio de parseo
    tolerante que `_detalle_desde_raw_json` (JSON ausente/corrupto o sin la
    clave `herramienta` nunca lanza, siempre degrada). Ausencia de
    `herramienta` se trata como ZAP (alertas planas de escaneo activo
    checkpointed, o `zap-baseline`/`zap-full-scan` con wrapper -- ambos
    comparten el mismo modo single-target nuevo)."""
    if not raw_json:
        return "zap"
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return "zap"
    if not isinstance(data, dict):
        return "zap"
    herramienta = str(data.get("herramienta") or "").strip().lower()
    if herramienta == "nuclei":
        return "nuclei"
    if herramienta in ("semgrep", "trivy", "grype"):
        return herramienta
    return "zap"


_HERRAMIENTAS_SIN_ENDPOINT_REESCANEABLE = ("semgrep", "trivy", "grype")


def _nombre_de_alerta(alerta: dict) -> str:
    """Extrae el nombre/tipo de una alerta cruda de re-escaneo, tolerante al
    formato -- ZAP (`get_alerts()`) trae `name`/`alert` en el nivel
    superior; Nuclei (`run_nuclei().findings`) trae el nombre de la
    plantilla bajo `info.name`. Nunca lanza -- degrada a cadena vacía si no
    se reconoce ningún campo."""
    if not isinstance(alerta, dict):
        return ""
    nombre = alerta.get("name") or alerta.get("alert")
    if not nombre:
        info = alerta.get("info")
        if isinstance(info, dict):
            nombre = info.get("name")
    return str(nombre or "").strip().lower()


def _alertas_que_coinciden_con_finding(alertas: list[dict], tipo_finding: str) -> list[dict]:
    """Filtra las alertas de un re-escaneo puntual a solo las que coinciden
    con `finding.tipo` -- el mismo valor que `_registrar_findings_con_ciclo_
    de_vida` usa para identificar un hallazgo por `(tipo, endpoint)` (ver
    ese helper, y `_correr_escaneo_activo_en_background` que puebla `tipo`
    con `alerta.get("name")` para hallazgos de origen ZAP).

    Existe porque un re-escaneo puntual de un endpoint puede legítimamente
    devolver alertas que NO tienen nada que ver con el finding que se está
    verificando (ej. un endpoint HTTP casi siempre dispara la alerta pasiva
    de ZAP "HTTP Only Site" sin importar qué se arregló) -- contar
    cualquier alerta como "reaparece" bloquearía el cierre automático para
    siempre en esos casos. Si `tipo_finding` viene vacío o "desconocido"
    (no hay con qué comparar de forma confiable), degrada al comportamiento
    anterior -- cualquier alerta cuenta -- en vez de fallar silenciosamente
    tratando todo como "no reaparece"."""
    tipo_normalizado = (tipo_finding or "").strip().lower()
    if not tipo_normalizado or tipo_normalizado == "desconocido":
        return list(alertas)
    return [a for a in alertas if _nombre_de_alerta(a) == tipo_normalizado]


def _registrar_transicion_verificacion(
    conn, finding_id: str, tenant_id: str, changed_by: str, estado_anterior: str, estado_nuevo: str, nota: str, now: str
) -> None:
    """Inserta una fila de auditoría en `finding_transiciones` para un cambio
    de estado disparado por `POST /findings/{id}/verificar-cierre` --
    `changed_by` es el usuario que llamó al endpoint (a diferencia de la
    detección de reincidencia en `_registrar_findings_con_ciclo_de_vida`,
    que deja `changed_by=NULL` porque la dispara el sistema al insertar un
    scan nuevo, sin que nadie lo haya pedido explícitamente). Estas filas
    son las que `GET /kpis` usa para MTTR real (ver
    `_calcular_mttr_por_severidad`)."""
    conn.execute(
        """
        INSERT INTO finding_transiciones
            (id, finding_id, tenant_id, estado_anterior, estado_nuevo, nota, changed_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), finding_id, tenant_id, estado_anterior, estado_nuevo, nota, changed_by, now),
    )


@app.post("/findings/{finding_id}/verificar-cierre", response_model=VerificarCierreOut)
def verificar_cierre_finding(
    finding_id: str, user: UserContext = Depends(get_current_user)
) -> VerificarCierreOut:
    """Re-escanea ÚNICAMENTE el `endpoint` del hallazgo (no el asset
    completo) y, si la alerta original ya no aparece, transiciona el
    hallazgo `verificado` -> `cerrado` automáticamente.

    Dispatch por herramienta de origen (`_herramienta_de_finding`): nuclei
    re-invoca `run_nuclei(endpoint)` directo (ya es single-target por
    diseño); ZAP (baseline o alertas planas de escaneo activo) usa el modo
    nuevo `run_single_target_verification`; semgrep/trivy/grype responden
    422 -- no tienen un endpoint HTTP puntual que re-escanear (código/
    imagen, no URL), no se finge soporte.

    A propósito NO llama al `PATCH /findings/{id}` de `fase2a` -- dos
    `UPDATE` secuenciales directos sobre `estado` (mismos valores del CHECK
    constraint de `db/schema.sql`) para no acoplar este endpoint a la firma
    de esa API (ver design.md, Decision "Transición de estado reusa el
    vocabulario de fase2a, no inventa uno nuevo"). Sí registra su propia
    fila en `finding_transiciones` por cada `UPDATE` -- necesario para que
    `GET /kpis` pueda calcular MTTR real a partir de estas transiciones
    (igual que ya hace `fase2a` para sus propios cambios vía PATCH).

    Mismo scoping/404 que `GET /findings/{id}`: un hallazgo inexistente o
    de otro tenant responde 404, nunca 403.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM findings WHERE id = ? AND tenant_id = ?", (finding_id, user.tenant_id)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Hallazgo no encontrado")
        finding = dict_from_row(row)
    finally:
        conn.close()

    herramienta = _herramienta_de_finding(finding.get("raw_json"))
    if herramienta in _HERRAMIENTAS_SIN_ENDPOINT_REESCANEABLE:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Verificación de cierre automática no soportada para hallazgos de origen "
                f"'{herramienta}': no tienen un endpoint HTTP puntual que re-escanear "
                "(código fuente o imagen de contenedor, no una URL). Cambia el estado "
                f"manualmente vía PATCH /findings/{finding_id} tras confirmar la remediación."
            ),
        )

    estado_actual = finding["estado"]
    if estado_actual in ("cerrado", "aceptado_riesgo"):
        return VerificarCierreOut(
            finding_id=finding_id,
            estado=estado_actual,
            reaparece=False,
            detalle=f"Este hallazgo ya está en estado terminal ('{estado_actual}') -- no se volvió a escanear.",
        )

    endpoint = finding["endpoint"]
    try:
        if herramienta == "nuclei":
            alertas = run_nuclei(endpoint).findings
        else:
            zap_resultado = run_single_target_verification(
                target_url=endpoint,
                endpoint_url=endpoint,
                bearer_token=None,
                scan_id=finding_id,
            )
            alertas = zap_resultado.findings
    except ToolExecutionError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo re-escanear el endpoint para verificar el cierre: {exc}",
        )

    # Bug real encontrado corriendo esta verificación en vivo contra ZAP +
    # Juice Shop real (no visible con los mocks existentes, que nunca
    # devolvían más de una alerta a la vez): `get_alerts()`/`run_nuclei()`
    # devuelven TODAS las alertas presentes en ese endpoint, no solo la que
    # originó este finding. Un endpoint sobre HTTP (no HTTPS) casi siempre
    # tiene alertas pasivas genéricas de ZAP ("HTTP Only Site",
    # "Cross-Domain Misconfiguration") que nunca desaparecen -- si
    # `reaparece` se calculaba como "hay alguna alerta" (`len(alertas) > 0`),
    # NINGÚN finding de origen ZAP sobre un target HTTP podía cerrarse jamás
    # de forma automática, sin importar si la vulnerabilidad específica de
    # ESE finding ya se arregló. Se filtra a las alertas cuyo nombre
    # coincide con `finding["tipo"]` (mismo valor que ya usa
    # `_registrar_findings_con_ciclo_de_vida` para identificar un hallazgo
    # por `(tipo, endpoint)`) antes de decidir si reaparece.
    alertas_coincidentes = _alertas_que_coinciden_con_finding(alertas, finding["tipo"])
    reaparece = len(alertas_coincidentes) > 0
    now = datetime.now(timezone.utc).isoformat()

    if not reaparece:
        conn = get_conn()
        try:
            # Salta el paso intermedio si el hallazgo YA estaba en
            # 'verificado' (ej. alguien lo verificó a mano vía PATCH antes
            # de llamar a este endpoint) -- evita una fila de auditoría
            # auto-referencial ('verificado' -> 'verificado') sin sentido.
            if estado_actual != "verificado":
                conn.execute("UPDATE findings SET estado = 'verificado' WHERE id = ?", (finding_id,))
                _registrar_transicion_verificacion(
                    conn, finding_id, user.tenant_id, user.user_id, estado_actual, "verificado",
                    "Re-escaneo puntual de verificación de cierre no encontró la alerta original.", now,
                )
            conn.execute("UPDATE findings SET estado = 'cerrado' WHERE id = ?", (finding_id,))
            _registrar_transicion_verificacion(
                conn, finding_id, user.tenant_id, user.user_id, "verificado", "cerrado",
                "Verificación de cierre automática confirmó ausencia de la alerta.", now,
            )
            conn.commit()
        finally:
            conn.close()
        return VerificarCierreOut(
            finding_id=finding_id,
            estado="cerrado",
            reaparece=False,
            detalle=f"El hallazgo no reprodujo en el re-escaneo puntual de '{endpoint}'; transicionado a verificado -> cerrado.",
        )

    # Reaparece: refresca `estado_verificacion`/`cves_detectados`/
    # `evidencia_exploitdb` dentro de raw_json con la alerta encontrada
    # ahora, sin tocar `estado` (task 2.4 de tasks.md). Usa
    # `alertas_coincidentes[0]` (no `alertas[0]`) -- de lo contrario, si el
    # endpoint tiene además otras alertas no relacionadas, se podría
    # refrescar el finding con evidencia de una vulnerabilidad distinta a
    # la que este finding representa.
    verificado = verificar_un_hallazgo(alertas_coincidentes[0])
    try:
        data = json.loads(finding.get("raw_json") or "{}")
        if not isinstance(data, dict):
            data = {}
    except (json.JSONDecodeError, TypeError):
        data = {}
    data["estado_verificacion"] = verificado.get("estado_verificacion")
    data["cves_detectados"] = verificado.get("cves_detectados") or []
    data["evidencia_exploitdb"] = verificado.get("evidencia_exploitdb") or []

    conn = get_conn()
    try:
        conn.execute(
            "UPDATE findings SET raw_json = ? WHERE id = ?", (json.dumps(data, default=str), finding_id)
        )
        conn.commit()
    finally:
        conn.close()

    return VerificarCierreOut(
        finding_id=finding_id,
        estado=estado_actual,
        reaparece=True,
        detalle=f"El hallazgo sigue reproduciendo en el re-escaneo puntual de '{endpoint}'; estado sin cambios.",
    )


SEVERIDADES_KPI: tuple[str, ...] = ("critical", "high", "medium", "low", "info")


class KpiSeveridad(BaseModel):
    severidad: str
    mttr_dias: Optional[float] = None
    # `fase2a-ciclo-vida-hallazgo` ya está aplicado y persiste
    # `finding_transiciones.created_at` real (no aproximado) en cada
    # transición -- manual (PATCH) o automática (drift/verificar-cierre) --
    # así que el MTTR de este endpoint es "exacto" siempre, no la
    # aproximación con `ultima_vez_visto`/`created_at` que design.md había
    # dejado documentada como fallback por si esa tabla no existiera (ver
    # design.md, Open Question #1, actualizado en esta sesión).
    precision: str = "exacto"


class KpiTendenciaAsset(BaseModel):
    asset_id: str
    puntos: list[dict]


class KpiOut(BaseModel):
    mttr_por_severidad: list[KpiSeveridad]
    porcentaje_dentro_sla: Optional[float] = None
    tendencia_por_activo: list[KpiTendenciaAsset]


def _parse_dt_utc_naive(valor: str) -> datetime:
    """Parsea un timestamp de la DB (ISO con offset, ej.
    `datetime.now(timezone.utc).isoformat()`; o el formato sin offset que
    deja `DEFAULT CURRENT_TIMESTAMP` de SQLite/Postgres tal como los
    normaliza `db/connection.py`) a un `datetime` naive en UTC.

    Necesario porque `finding_transiciones.created_at` puede venir en
    cualquiera de los dos formatos según cómo se insertó la fila (ver
    `actualizar_finding` -- deja el DEFAULT -- vs.
    `_registrar_findings_con_ciclo_de_vida`/`_registrar_transicion_verificacion`
    -- insertan `now` explícito con offset), y restar un `datetime` aware
    contra uno naive lanza `TypeError` en Python. Ambos orígenes son UTC de
    hecho; esto solo unifica la representación, no cambia el instante real.
    """
    dt = datetime.fromisoformat(valor)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _calcular_mttr_por_severidad(findings: list[dict], primer_cierre: dict[str, str]) -> list[KpiSeveridad]:
    """MTTR real por severidad: para cada finding con al menos una fila en
    `finding_transiciones` donde `estado_nuevo='cerrado'`, el delta es esa
    transición (la más temprana, primer cierre) menos `findings.created_at`
    -- ambos timestamps reales. Severidades sin ningún hallazgo cerrado
    devuelven `mttr_dias=None` (spec.md: nunca `0`, nunca se omite la
    severidad)."""
    deltas: dict[str, list[float]] = {sev: [] for sev in SEVERIDADES_KPI}
    for finding in findings:
        cerrado_en = primer_cierre.get(finding["id"])
        if cerrado_en is None:
            continue
        try:
            delta_dias = (
                _parse_dt_utc_naive(cerrado_en) - _parse_dt_utc_naive(finding["created_at"])
            ).total_seconds() / 86400.0
        except (TypeError, ValueError):
            continue
        deltas.setdefault(finding["severidad"], []).append(max(delta_dias, 0.0))

    resultado: list[KpiSeveridad] = []
    for sev in SEVERIDADES_KPI:
        valores = deltas.get(sev) or []
        mttr = (sum(valores) / len(valores)) if valores else None
        resultado.append(KpiSeveridad(severidad=sev, mttr_dias=mttr, precision="exacto"))
    return resultado


def _calcular_porcentaje_sla(findings: list[dict]) -> Optional[float]:
    """% de hallazgos abiertos (`estado` distinto de `cerrado`/
    `aceptado_riesgo`) cuyo `sla_due_at` aún no venció. Sin plazo definido
    (`sla_due_at IS NULL`, ej. severidad `low`/`info` sin config de tenant)
    se cuenta como dentro de plazo -- mismo criterio que `badgeSla` del
    frontend (`RemediationBoard.tsx`): "sin SLA" nunca es "vencido"."""
    abiertos = [f for f in findings if f["estado"] not in ("cerrado", "aceptado_riesgo")]
    if not abiertos:
        return None
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    dentro = 0
    for f in abiertos:
        sla = f.get("sla_due_at")
        if not sla:
            dentro += 1
            continue
        try:
            vence = _parse_dt_utc_naive(sla)
        except (TypeError, ValueError):
            dentro += 1
            continue
        if vence >= ahora:
            dentro += 1
    return round(100.0 * dentro / len(abiertos), 2)


def _calcular_tendencia_por_activo(filas: list[dict]) -> list[KpiTendenciaAsset]:
    """Serie temporal de conteo de hallazgos ABIERTOS por severidad, un
    punto por scan (fecha) de cada asset -- `filas` ya viene de un JOIN
    `scans`+`findings` (ver `obtener_kpis`), ordenado por
    `asset_id, scan_fecha` ascendente. Varios findings pueden compartir
    scan/fecha -- se agregan antes de emitir un punto por fecha."""
    por_asset: dict[str, dict[str, dict[str, int]]] = {}
    for fila in filas:
        asset_id = fila["asset_id"]
        fecha = fila["scan_fecha"]
        severidad = fila["severidad"]
        puntos_de_asset = por_asset.setdefault(asset_id, {})
        conteo = puntos_de_asset.setdefault(fecha, {sev: 0 for sev in SEVERIDADES_KPI})
        conteo[severidad] = conteo.get(severidad, 0) + 1

    resultado: list[KpiTendenciaAsset] = []
    for asset_id, puntos_por_fecha in por_asset.items():
        puntos = [{"fecha": fecha, **conteo} for fecha, conteo in puntos_por_fecha.items()]
        resultado.append(KpiTendenciaAsset(asset_id=asset_id, puntos=puntos))
    return resultado


@app.get("/kpis", response_model=KpiOut)
def obtener_kpis(user: UserContext = Depends(get_current_user)) -> KpiOut:
    """KPIs de remediación agregados sobre `findings`/`finding_transiciones`
    (solo lectura, scoped por tenant, sin ETL ni tabla nueva): MTTR real por
    severidad, % de hallazgos abiertos dentro de SLA, y tendencia de riesgo
    por activo a lo largo de sus escaneos. Ver design.md para el porqué del
    cálculo en Python (portabilidad SQLite/Postgres sin `julianday()`/
    `EXTRACT`)."""
    conn = get_conn()
    try:
        findings_rows = conn.execute(
            "SELECT id, severidad, estado, created_at, sla_due_at FROM findings WHERE tenant_id = ?",
            (user.tenant_id,),
        ).fetchall()
        cierres_rows = conn.execute(
            """
            SELECT finding_id, MIN(created_at) AS cerrado_en
            FROM finding_transiciones
            WHERE tenant_id = ? AND estado_nuevo = 'cerrado'
            GROUP BY finding_id
            """,
            (user.tenant_id,),
        ).fetchall()
        tendencia_rows = conn.execute(
            """
            SELECT s.asset_id AS asset_id, s.created_at AS scan_fecha, f.severidad AS severidad
            FROM scans s JOIN findings f ON f.scan_id = s.id
            WHERE s.tenant_id = ? AND s.asset_id IS NOT NULL
              AND f.estado NOT IN ('cerrado', 'aceptado_riesgo')
            ORDER BY s.asset_id, s.created_at
            """,
            (user.tenant_id,),
        ).fetchall()
    finally:
        conn.close()

    findings = [dict_from_row(r) for r in findings_rows]
    cierres = [dict_from_row(r) for r in cierres_rows]
    primer_cierre = {fila["finding_id"]: fila["cerrado_en"] for fila in cierres}
    tendencia_filas = [dict_from_row(r) for r in tendencia_rows]

    return KpiOut(
        mttr_por_severidad=_calcular_mttr_por_severidad(findings, primer_cierre),
        porcentaje_dentro_sla=_calcular_porcentaje_sla(findings),
        tendencia_por_activo=_calcular_tendencia_por_activo(tendencia_filas),
    )


class CumplimientoCategoriaOut(BaseModel):
    categoria: str
    nombre: str
    cantidad: int
    iso27001: list[str]
    ley2573_obligaciones: list[dict]
    explicacion: str
    ejemplos: list[dict]


class CumplimientoReportOut(BaseModel):
    reporte_markdown: str
    resumen_por_categoria: list[CumplimientoCategoriaOut]
    cobertura_ley2573: dict
    advertencia_alcance_legal: str
    advertencia_iso27001: str
    generado_en: str
    total_hallazgos: int


@app.get("/reports/cumplimiento", response_model=CumplimientoReportOut)
def reporte_cumplimiento(
    contexto_negocio: str = "",
    user: UserContext = Depends(get_current_user),
) -> CumplimientoReportOut:
    """Reporte de cumplimiento normativo (Ley 2573 de 2026 / ISO 27001) — Item 6 del backlog.

    Distinto de `POST /scan` (reporte técnico de UN escaneo, dentro del
    grafo): este endpoint lee TODO el historial de `findings` del tenant
    autenticado — a través de todos sus scans — porque el argumento de
    venta (`docs/market-research.md` sección 3) es evidencia acumulada de
    trazabilidad, no una foto de un solo momento. La lógica de
    categorización y mapeo vive en `agents/cumplimiento.py` (no es un nodo
    del grafo LangGraph, se invoca directo desde aquí bajo demanda).
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM findings WHERE tenant_id = ? ORDER BY created_at DESC",
            (user.tenant_id,),
        ).fetchall()
    finally:
        conn.close()

    findings = [dict_from_row(r) for r in rows]
    resultado = generar_reporte_cumplimiento(findings, contexto_negocio=contexto_negocio)

    return CumplimientoReportOut(
        reporte_markdown=resultado["reporte_markdown"],
        resumen_por_categoria=resultado["resumen_por_categoria"],
        cobertura_ley2573=resultado["cobertura_ley2573"],
        advertencia_alcance_legal=resultado["advertencia_alcance_legal"],
        advertencia_iso27001=resultado["advertencia_iso27001"],
        generado_en=resultado["generado_en"],
        total_hallazgos=len(findings),
    )


@app.get("/reports/cumplimiento/download")
def reporte_cumplimiento_download(
    formato: str = "pdf",
    contexto_negocio: str = "",
    user: UserContext = Depends(get_current_user),
) -> Response:
    """Descarga del reporte de cumplimiento como PDF o DOCX (item transversal de HANDOFF.md).

    Generación 100% server-side: el markdown que ya produce
    `generar_reporte_cumplimiento` se convierte a bytes descargables con
    `tools/report_export.py` (fpdf2/python-docx, sin binarios de sistema) —
    así la descarga funciona igual sin importar el navegador del cliente.
    """
    if formato not in ("pdf", "docx"):
        raise HTTPException(status_code=422, detail="formato debe ser 'pdf' o 'docx'")

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM findings WHERE tenant_id = ? ORDER BY created_at DESC",
            (user.tenant_id,),
        ).fetchall()
    finally:
        conn.close()

    findings = [dict_from_row(r) for r in rows]
    resultado = generar_reporte_cumplimiento(findings, contexto_negocio=contexto_negocio)
    titulo = "Reporte de Cumplimiento — Vigia"

    if formato == "pdf":
        contenido = markdown_to_pdf_bytes(resultado["reporte_markdown"], titulo)
        media_type = "application/pdf"
        filename = "reporte-cumplimiento.pdf"
    else:
        contenido = markdown_to_docx_bytes(resultado["reporte_markdown"], titulo)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = "reporte-cumplimiento.docx"

    return Response(
        content=contenido,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/health")
def health() -> dict:
    """Healthcheck simple. No toca el grafo ni ninguna herramienta externa."""
    return {
        "status": "ok",
        "servicio": "ciberseguridad-ia-api",
        "fase": "Fase 1 (MVP) — sección 5 del plan maestro",
        "anthropic_api_key_configurada": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "aviso_legal": AVISO_LEGAL,
    }


def _extraer_findings_antisuplantacion(target: str, antisuplantacion_findings: list[dict]) -> list[dict]:
    """Aplana `antisuplantacion_findings` (salida de `agents/antisuplantacion.py`) a filas insertables en `findings`.

    Brecha real documentada en `docs/cumplimiento.md` y `HANDOFF.md` (Item 6):
    `agents/antisuplantacion.py` (dnstwist bajo demanda + Sherlock) SÍ produce
    señales reales dentro del `PipelineState` (`antisuplantacion_findings`),
    pero hasta ahora esta función nunca las insertaba en la tabla `findings`
    — solo `verified_findings` (rama técnica) se persistía aquí, y solo
    `api/certstream_listener.py::registrar_finding_certstream()` persistía
    señales de suplantación (vigilancia continua, no bajo demanda). Como
    resultado, `GET /findings` y `GET /reports/cumplimiento` nunca veían
    resultados de un `POST /scan` con `antisuplantacion_habilitado=true`.

    No hace falta una fila `scans` sintética como en CertStream: este
    `POST /scan` YA crea una fila `scans` real para toda la corrida del
    grafo (ver más abajo), así que estas señales simplemente se cuelgan del
    mismo `scan_id` real, igual que `verified_findings`.

    Usa los mismos `tipo` que ya reconoce `agents/cumplimiento.py::_categorizar_hallazgo()`
    (`"dominio_variante"` -> categoría `suplantacion_dominio`, `"perfil_red_social"`
    -> categoría `suplantacion_redes_sociales`) sin necesidad de tocar ese
    módulo — la brecha era de persistencia, no de categorización (esa ya
    estaba lista, solo nunca recibía datos).

    `confirmado=0` siempre, mismo criterio que `registrar_finding_certstream()`:
    una variante de dominio registrada o un perfil con el mismo username son
    señales fuertes, no una confirmación determinista de suplantación activa
    (podría ser el propio cliente o una coincidencia legítima) — es Claude
    quien evalúa "suplantación real vs. coincidencia" en `analisis_claude`,
    que viaja completo en `raw_json` para trazabilidad.
    """
    target_normalizado = (target or "").strip().lower()
    filas: list[dict] = []
    for hallazgo in antisuplantacion_findings or []:
        analisis_claude = hallazgo.get("analisis_claude")
        for señal in hallazgo.get("señales_crudas") or []:
            tipo_señal = señal.get("tipo")
            if tipo_señal == "dominio_variante":
                # Bug real encontrado al verificar esto en vivo contra
                # microsoft.com: `agents/antisuplantacion.py::node()` NO
                # excluye la entrada `fuzzer='*original'` que `run_dnstwist()`
                # siempre incluye (a diferencia de `generate_domain_variants()`,
                # que sí la excluye) — es el propio dominio del cliente, ya
                # registrado por definición. Sin este filtro se habría
                # persistido un falso positivo real: el dominio del cliente
                # marcado como "posible dominio variante" de sí mismo. Mismo
                # criterio que ya usa `api/certstream_listener.py` (`if apex
                # == dominio_base ...: continue`).
                if señal.get("fuzzer") == "*original" or (señal.get("dominio") or "").strip().lower() == target_normalizado:
                    continue
                # Defensa en profundidad: `run_dnstwist(..., registered_only=True)`
                # ya filtra esto aguas arriba, pero no confiamos únicamente en
                # eso (mismo espíritu que la doble verificación de
                # `autorizacion_firmada` en `agents/escaneo.py`).
                if not señal.get("registrado"):
                    continue
                endpoint = señal.get("dominio") or ""
                severidad = "high"
            elif tipo_señal == "perfil_red_social":
                endpoint = señal.get("url") or ""
                severidad = "medium"
            else:
                continue  # señal sin schema reconocido; no persistir a ciegas
            filas.append(
                {
                    "tipo": tipo_señal,
                    "severidad": severidad,
                    "endpoint": endpoint,
                    "raw_json": {
                        "señal": señal,
                        "target": target,
                        "analisis_claude": analisis_claude,
                        "fuente": "antisuplantacion_bajo_demanda",
                    },
                }
            )
    return filas


# ---------------------------------------------------------------------------
# Ciclo de vida del hallazgo (openspec/changes/fase2a-ciclo-vida-hallazgo):
# máquina de estados de `PATCH /findings/{id}`, cálculo de `sla_due_at` por
# severidad al insertar, y detección de reincidencia (drift) contra
# hallazgos existentes del mismo asset. Ver design.md, Data Flow.
# ---------------------------------------------------------------------------

# Contrato exacto fijado en design.md -- no cambia sin nueva ronda de diseño
# porque `fase2b-tablero-remediacion` (frontend, en paralelo) construye
# contra este contrato de PATCH sin ver código.
TRANSICIONES_VALIDAS: dict[str, set[str]] = {
    "descubierto": {"triage", "aceptado_riesgo"},
    "triage": {"asignado", "aceptado_riesgo"},
    "asignado": {"en_progreso", "aceptado_riesgo"},
    "en_progreso": {"mitigado", "aceptado_riesgo"},
    "mitigado": {"verificado", "aceptado_riesgo"},
    "verificado": {"cerrado", "aceptado_riesgo"},
    "cerrado": set(),
    "aceptado_riesgo": set(),
}

# Días de plazo por severidad -- default embebido, usado cuando el tenant no
# tiene una fila propia en `tenant_sla_config`. `None` = sin plazo (best-effort).
DEFAULT_SLA_DIAS: dict[str, int | None] = {
    "critical": 7,
    "high": 30,
    "medium": 90,
    "low": None,
    "info": None,
}


def _calcular_sla_due_at(conn, tenant_id: str, severidad: str, created_at: str) -> str | None:
    """`sla_due_at = created_at + N días` según `severidad`, usando la config
    propia del tenant (`tenant_sla_config`) si existe, si no `DEFAULT_SLA_DIAS`.

    `created_at` es un string ISO 8601 (mismo formato que ya produce el resto
    de este archivo, `datetime.now(timezone.utc).isoformat()`).
    """
    dias_por_severidad = dict(DEFAULT_SLA_DIAS)
    config_row = conn.execute(
        "SELECT * FROM tenant_sla_config WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    if config_row is not None:
        config = dict_from_row(config_row)
        dias_por_severidad["critical"] = config["dias_critical"]
        dias_por_severidad["high"] = config["dias_high"]
        dias_por_severidad["medium"] = config["dias_medium"]
        dias_por_severidad["low"] = config["dias_low"]

    dias = dias_por_severidad.get(severidad)
    if dias is None:
        return None
    return (datetime.fromisoformat(created_at) + timedelta(days=dias)).isoformat()


def _registrar_findings_con_ciclo_de_vida(
    conn,
    tenant_id: str,
    scan_id: str,
    asset_id: Optional[str],
    hallazgos: list[dict],
    now: str,
) -> None:
    """Inserta o actualiza findings aplicando SLA (por severidad) y detección
    de reincidencia (drift) contra hallazgos previos del mismo asset -- ver
    design.md, Data Flow. Compartida por ambos paths de inserción (`scan()` y
    `_correr_escaneo_activo_en_background`) para que esta lógica viva en un
    solo lugar.

    `hallazgos` ya viene normalizado por el llamador: cada dict trae `tipo`,
    `severidad`, `endpoint`, `confirmado` (bool/int), `raw_json` (string JSON
    ya serializado) -- exactamente los mismos valores que antes se pasaban
    directo al `INSERT INTO findings` en cada call site.

    `asset_id` puede ser `None` (scan sin asset resoluble) -- la subquery de
    abajo (`scans.asset_id = ? AND asset_id IS NOT NULL`) nunca matchea con
    parámetro `None` (comparación con NULL en SQL), así que todo hallazgo se
    trata como nuevo, nunca como reincidencia falsa (ver spec.md, escenario
    "Scan sin asset resoluble no participa del drift").
    """
    for hallazgo in hallazgos:
        tipo = hallazgo["tipo"]
        endpoint = hallazgo["endpoint"]
        match = conn.execute(
            """
            SELECT id, estado FROM findings
            WHERE tenant_id = ? AND tipo = ? AND endpoint = ?
              AND scan_id IN (
                  SELECT id FROM scans WHERE asset_id = ? AND asset_id IS NOT NULL
              )
            """,
            (tenant_id, tipo, endpoint, asset_id),
        ).fetchone()

        if match is not None:
            match = dict_from_row(match)
            if match["estado"] in ("cerrado", "verificado", "aceptado_riesgo"):
                # Reincidencia: el hallazgo había sido cerrado/verificado/
                # aceptado y volvió a aparecer -- se reabre y se deja rastro
                # en finding_transiciones (changed_by=NULL -- lo hizo el
                # sistema, no un usuario vía PATCH).
                conn.execute(
                    "UPDATE findings SET estado = 'descubierto', ultima_vez_visto = ? WHERE id = ?",
                    (now, match["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO finding_transiciones
                        (id, finding_id, tenant_id, estado_anterior, estado_nuevo,
                         owner_anterior, owner_nuevo, nota, changed_by, created_at)
                    VALUES (?, ?, ?, ?, 'descubierto', NULL, NULL, ?, NULL, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        match["id"],
                        tenant_id,
                        match["estado"],
                        "reincidencia detectada automáticamente",
                        now,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE findings SET ultima_vez_visto = ? WHERE id = ?",
                    (now, match["id"]),
                )
            continue

        sla_due_at = _calcular_sla_due_at(conn, tenant_id, hallazgo["severidad"], now)
        conn.execute(
            """
            INSERT INTO findings
                (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json,
                 created_at, sla_due_at, primera_vez_visto, ultima_vez_visto)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                scan_id,
                tenant_id,
                tipo,
                hallazgo["severidad"],
                endpoint,
                int(bool(hallazgo["confirmado"])),
                hallazgo["raw_json"],
                now,
                sla_due_at,
                now,
                now,
            ),
        )


@app.post("/scan", response_model=ScanResponse)
def scan(payload: ScanRequest, user: UserContext = Depends(get_current_user)) -> ScanResponse:
    """Dispara el pipeline completo (`orchestrator.graph`) para `target`, para el tenant autenticado.

    El grafo (`gate_autorizacion`, sección 8.1) sigue siendo la única fuente
    de verdad sobre si `autorizacion_firmada=true` desbloquea Escaneo
    Activo -- eso NO cambia aquí. Lo que sí se agrega esta sesión (mismo gap
    real que ya se había cerrado para `POST /scan/activo`, ver
    `_asset_verificado_para_target`) es una pregunta anterior y distinta:
    ¿`target` es siquiera un activo que este tenant registró Y verificó
    (o está exento por ser localhost/IP privada)? Antes de este cambio,
    CUALQUIER string en `target` -- incluido un dominio de un tercero real,
    ej. 'microsoft.com' -- entraba al pipeline completo (recon pasivo,
    anti-suplantación) con solo un JWT válido, sin que el tenant hubiera
    declarado ni probado ningún vínculo con ese dominio. Rechazar esto aquí,
    antes de invocar el grafo, evita ese gasto (llamadas a Claude,
    subfinder/amass) contra un target no autorizado, además de cerrar el
    hueco de autorización en sí.
    """
    conn_check = get_conn()
    try:
        autorizado, motivo, asset = _asset_verificado_para_target(conn_check, user.tenant_id, payload.target)
    finally:
        conn_check.close()
    if not autorizado:
        raise HTTPException(status_code=403, detail=motivo)

    estado_inicial = new_state(
        target=payload.target,
        autorizacion_firmada=payload.autorizacion_firmada,
        scope=payload.scope.model_dump(),
        contexto_negocio=payload.contexto_negocio,
        antisuplantacion_habilitado=payload.antisuplantacion_habilitado,
    )

    grafo = _get_graph()
    estado_final = grafo.invoke(estado_inicial)

    verified = estado_final.get("verified_findings") or []
    scan_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO scans
                (id, tenant_id, asset_id, target, autorizacion_firmada, estado, reporte_final, trace_log_json, completed_at)
            VALUES (?, ?, ?, ?, ?, 'completado', ?, ?, ?)
            """,
            (
                scan_id,
                user.tenant_id,
                # `asset` ya viene resuelto por `_asset_verificado_para_target` de
                # arriba (garantizado no-None a esta altura -- si fuera None,
                # `autorizado` habría sido False y ya se habría lanzado el 403) --
                # antes de esta sesión se descartaba (`_asset`) y `scans.asset_id`
                # nunca se poblaba, dejando el drift de fase2a sin llave de scoping
                # (ver design.md, "Poblar scans.asset_id").
                asset["id"] if asset else None,
                estado_final.get("target", payload.target),
                int(bool(estado_final.get("autorizacion_firmada", payload.autorizacion_firmada))),
                estado_final.get("reporte_final"),
                json.dumps(estado_final.get("trace_log") or []),
                now,
            ),
        )

        # Normaliza cada hallazgo verificado a la forma que espera
        # `_registrar_findings_con_ciclo_de_vida` -- mismo cálculo de
        # tipo/severidad/endpoint/confirmado que antes se pasaba directo al
        # INSERT (ver comentario histórico sobre el bug de severidad/endpoint
        # en tools/_shared.py::normalize_severity), ahora centralizado para
        # aplicar también SLA + drift antes de tocar `findings`.
        hallazgos_normalizados = [
            {
                "tipo": hallazgo.get("tipo") or hallazgo.get("type") or "desconocido",
                "severidad": normalize_severity(hallazgo.get("raw"), hallazgo.get("herramienta", "")),
                "endpoint": hallazgo.get("endpoint") or hallazgo.get("objetivo") or "",
                "confirmado": bool(hallazgo.get("confirmado")),
                "raw_json": json.dumps(hallazgo),
            }
            for hallazgo in verified
        ]

        # Cierra la brecha documentada en docs/cumplimiento.md / HANDOFF.md
        # (Item 6): las señales reales de dnstwist/Sherlock bajo demanda
        # ahora también se persisten, colgadas del mismo scan_id real.
        antisuplantacion_rows = _extraer_findings_antisuplantacion(
            estado_final.get("target", payload.target),
            estado_final.get("antisuplantacion_findings") or [],
        )
        hallazgos_normalizados.extend(
            {
                "tipo": fila["tipo"],
                "severidad": fila["severidad"],
                "endpoint": fila["endpoint"],
                "confirmado": False,
                "raw_json": json.dumps(fila["raw_json"], default=str),
            }
            for fila in antisuplantacion_rows
        )

        _registrar_findings_con_ciclo_de_vida(
            conn, user.tenant_id, scan_id, asset["id"] if asset else None, hallazgos_normalizados, now
        )

        conn.commit()
    finally:
        conn.close()

    return ScanResponse(
        target=estado_final.get("target", payload.target),
        autorizacion_firmada=estado_final.get("autorizacion_firmada", payload.autorizacion_firmada),
        autorizacion_bloqueo_motivo=estado_final.get("autorizacion_bloqueo_motivo"),
        recon_findings=estado_final.get("recon_findings") or [],
        scan_findings=estado_final.get("scan_findings") or [],
        verified_findings=verified,
        prioritized_findings=estado_final.get("prioritized_findings") or [],
        remediations=estado_final.get("remediations") or [],
        antisuplantacion_findings=estado_final.get("antisuplantacion_findings") or [],
        reporte_final=estado_final.get("reporte_final"),
        trace_log=estado_final.get("trace_log") or [],
    )


class ActiveScanRequest(BaseModel):
    """Payload de `POST /scan/activo` — escaneo activo ZAP de larga duración, en background.

    Existe separado de `POST /scan` a propósito: un `zap-full-scan` con
    AJAX Spider puede tardar 30-90 minutos (probado en vivo, ver
    `eval/live_run_report.md` corrida 4) — bloquear un request HTTP ese
    tiempo no es viable. Este endpoint arranca el trabajo en un thread y
    devuelve de inmediato; el estado se consulta con `GET /scans/{id}`.
    """

    target_url: str = Field(..., description="URL completa, ej. 'http://miempresa.com'")
    minutes: int = Field(default=20, ge=1, le=180)
    ajax_spider: bool = Field(default=True)
    bearer_token: Optional[str] = Field(
        default=None, description="JWT/token de sesión ya obtenido, si se quiere cubrir rutas autenticadas"
    )
    autorizacion_firmada: bool = Field(
        ...,
        description=(
            "A diferencia de POST /scan, aquí SÍ se exige explícitamente en true — "
            "un escaneo activo de larga duración no debe poder arrancar 'por accidente' "
            "sin que quien llama confirme la autorización en el mismo request."
        ),
    )


class ActiveScanAccepted(BaseModel):
    scan_id: str
    estado: str = "corriendo"


def _hostname_de_target(target_url: str) -> str:
    """Extrae el hostname de una URL o de un 'dominio pelado' sin esquema.

    `urlparse` exige un esquema para poblar `.hostname` -- si `target_url`
    llega sin `http(s)://` (ej. 'miempresa.com' en vez de
    'http://miempresa.com'), se le antepone uno solo para el parseo. No se
    usa el resultado para nada más que extraer el host.
    """
    candidato = target_url if "://" in target_url else f"http://{target_url}"
    return (urlparse(candidato).hostname or "").lower()


def _buscar_asset_para_target(conn, tenant_id: str, target_url: str) -> dict | None:
    """Encuentra el asset (fila completa) del tenant que corresponde a `target_url`, si existe.

    Extraído de lo que antes era el cuerpo entero de
    `_tenant_tiene_asset_para_target` (ver esa función para la explicación
    completa de por qué esta pregunta es distinta de `gate_autorizacion`) --
    ahora devuelve la fila completa, no solo `bool`, porque el gate nuevo de
    verificación de propiedad (`_asset_verificado_para_target`, más abajo)
    necesita leer `verificado`/`tipo`/`valor` del asset que hizo match, no
    solo saber que "alguno" hizo match.

    Compara por dominio registrable (`tools.antisuplantacion.registrable_domain`,
    ya usado por el listener de CertStream) para que un asset 'miempresa.com'
    autorice también 'www.miempresa.com' o 'app.miempresa.com', y por
    coincidencia exacta de host para asset tipo 'ip'.
    """
    host = _hostname_de_target(target_url)
    if not host:
        return None
    target_apex = registrable_domain(host)

    rows = conn.execute(
        "SELECT * FROM assets WHERE tenant_id = ? AND is_active = 1",
        (tenant_id,),
    ).fetchall()
    for row in rows:
        asset = dict_from_row(row)
        tipo = asset["tipo"]
        valor = (asset["valor"] or "").strip().lower()
        if not valor:
            continue
        if tipo == "ip":
            if valor == host:
                return asset
            continue
        # 'dominio' o 'app': el valor registrado puede venir con o sin
        # esquema/ruta (ej. 'miempresa.com' o 'https://miempresa.com/app').
        asset_host = _hostname_de_target(valor) or valor
        if host == asset_host or registrable_domain(asset_host) == target_apex:
            return asset
    return None


def _tenant_tiene_asset_para_target(conn, tenant_id: str, target_url: str) -> bool:
    """Confirma que `target_url` corresponde a un asset que el tenant registró.

    Este check es DISTINTO de `orchestrator/graph.py::gate_autorizacion` a
    propósito, no una duplicación descuidada de esa lógica: `gate_autorizacion`
    resuelve una única pregunta ("¿el booleano `autorizacion_firmada` es
    `True`?") sobre el `PipelineState` del grafo completo. `POST /scan/activo`
    ya hace esa misma pregunta de forma directa (no pasa por el grafo -- ver
    docstring de `ActiveScanRequest`), así que reusar `gate_autorizacion` aquí
    significaría envolver el grafo entero (orquestador/recon/verificación/
    priorización/remediación) solo para leer un booleano, un costo real
    (llamadas a Claude, subfinder/amass) sin ningún beneficio de seguridad
    adicional -- la puerta "no negociable" de la sección 8.1 sigue siendo
    exclusivamente la de `tools/scan.py` invocado *desde* `agents/escaneo.py`
    dentro del grafo; esta ruta nunca pasa por ahí.

    Lo que SÍ faltaba (el hallazgo real de `agents/revision_ia.py`) es una
    pregunta que `gate_autorizacion` nunca contesta ni podría contestar
    porque no tiene acceso a la DB ni al concepto de "asset": ¿este tenant
    en particular registró este target como suyo vía `POST /assets`, o
    cualquier cliente autenticado podría escanear cualquier URL con solo
    marcar un booleano en el body? Esa es una pregunta de propiedad de
    activo, no de autorización de escaneo -- se implementa como su propia
    función determinista y plana (mismo espíritu que `gate_autorizacion`:
    Python plano, sin LLM, testeable en aislamiento), en vez de forzarla
    dentro de una función que conceptualmente no la contiene.

    Deliberadamente NO exige verificación de propiedad -- esa es una
    pregunta distinta (ver `_asset_verificado_para_target`, agregada esta
    sesión), y esta función sigue siendo, a propósito, solo "¿existe un
    asset de este tenant que corresponda a este target?" para no romper su
    contrato original ni los tests que ya la ejercitan como tal.
    """
    return _buscar_asset_para_target(conn, tenant_id, target_url) is not None


def _asset_verificado_para_target(conn, tenant_id: str, target_url: str) -> tuple[bool, str, dict | None]:
    """La pregunta completa que de verdad debe gatear un escaneo: ¿hay asset Y está autorizado?

    Devuelve `(autorizado, motivo, asset_o_None)`. Tres resultados posibles:

    1. Ningún asset del tenant corresponde a `target_url` -- `(False, motivo_sin_asset, None)`.
       Mismo caso que ya cubría `_tenant_tiene_asset_para_target`.
    2. Hay asset, pero no está verificado ni exento (localhost/IP privada)
       -- `(False, motivo_falta_verificar, asset)`. Este es el gap nuevo que
       cierra esta sesión: antes, llegar hasta aquí bastaba para escanear.
    3. Hay asset Y (`verificado=True` O exento) -- `(True, "", asset)`.

    Ver `tools/asset_verification.py` para el razonamiento completo de la
    exención de localhost/IP privada -- deliberadamente no reimplementado
    aquí, esta función solo orquesta la búsqueda de asset + la pregunta de
    autorización, ya resuelta en esa capa más baja.
    """
    asset = _buscar_asset_para_target(conn, tenant_id, target_url)
    if asset is None:
        return (
            False,
            (
                f"'{target_url}' no corresponde a ningún activo registrado para este tenant. "
                "Regístralo primero con POST /assets."
            ),
            None,
        )
    if asset_autorizado_para_escanear(asset["tipo"], asset["valor"], bool(asset["verificado"])):
        return True, "", asset
    return (
        False,
        (
            f"El activo '{asset['valor']}' (id={asset['id']}) está registrado pero NO verificado. "
            "Antes de escanearlo, demuestra control real del dominio: agrega un registro DNS TXT en "
            f"'_vigia-challenge.{asset['valor']}' con el token de verificación, o publica ese mismo "
            f"token en 'https://{asset['valor']}/.well-known/vigia-verification.txt' (ver los campos "
            "'instrucciones_verificacion' de GET /assets para el token exacto), y luego llama a "
            f"POST /assets/{asset['id']}/verify con {{'metodo': 'dns_txt'}} o {{'metodo': 'http_file'}}. "
            "Excepción: activos que apuntan a 'localhost' o a un rango de IP privada no requieren "
            "este paso (ver tools/asset_verification.py)."
        ),
        asset,
    )


def _persistir_progreso_checkpoint(scan_id: str, progreso) -> None:
    """Guarda un checkpoint incremental de progreso mientras el escaneo sigue `corriendo`.

    Ver `tools/zap_api.py::run_checkpointed_active_scan` — reemplaza el
    bloqueo monolítico anterior (un único `subprocess.run` con timeout de
    hasta 35 min, sin visibilidad intermedia, ver eval/live_run_report.md
    Corridas 4 y 12) por polls HTTP cortos a la API real de ZAP. Este
    callback se invoca después de cada poll, así `GET /scans/{id}` puede
    reflejar progreso real ("spider: 57%", "escaneo activo: 12%") en vez
    de solo 'corriendo' durante toda la ventana de tiempo. Un fallo al
    persistir un checkpoint individual no debe tumbar el escaneo real —
    el próximo checkpoint lo intenta de nuevo.
    """
    conn = get_conn()
    try:
        texto = f"[{progreso.fase}] {progreso.detalle}"
        conn.execute("UPDATE scans SET reporte_final = ? WHERE id = ?", (texto, scan_id))
        conn.commit()
    except Exception:  # noqa: BLE001 — best-effort, ver docstring
        pass
    finally:
        conn.close()


def _correr_escaneo_activo_en_background(
    scan_id: str, tenant_id: str, asset_id: Optional[str], payload: ActiveScanRequest
) -> None:
    """Ejecuta el escaneo activo en un thread separado y actualiza la fila `scans` al terminar.

    Nunca lanza hacia el llamador (no hay llamador esperando) — cualquier
    error se captura y se guarda como `estado='error'` con el motivo en
    `reporte_final`, visible vía GET /scans/{id}.

    Usa `tools.zap_api.run_checkpointed_active_scan` (API real de ZAP,
    daemon + polling incremental) en vez de
    `tools.scan.run_zap_active_scan` (un único `docker run` bloqueante con
    timeout fijo) — ver HANDOFF.md Item 2 y el docstring de
    `tools/zap_api.py` para el razonamiento completo y la verificación en
    vivo contra Juice Shop real.
    """
    try:
        resultado = run_checkpointed_active_scan(
            payload.target_url,
            minutes=payload.minutes,
            bearer_token=payload.bearer_token,
            ajax_spider=payload.ajax_spider,
            scan_id=scan_id,
            on_progress=lambda progreso: _persistir_progreso_checkpoint(scan_id, progreso),
        )
        estado_final = "completado"
        reporte = f"Escaneo activo completado — {resultado.detalle_final}"
        hallazgos = resultado.findings
    except ToolExecutionError as exc:
        estado_final = "error"
        reporte = f"Escaneo activo falló: {exc}"
        hallazgos = []
    except Exception as exc:  # noqa: BLE001 — este thread es el último punto de captura posible
        estado_final = "error"
        reporte = f"Escaneo activo falló con un error inesperado: {exc}"
        hallazgos = []

    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE scans SET estado = ?, reporte_final = ?, completed_at = ? WHERE id = ?",
            (estado_final, reporte, now, scan_id),
        )

        # Fase 1 (openspec/changes/fase1-deteccion-visible): antes de ese
        # cambio, cada alerta se insertaba con `confirmado=0` fijo -- el
        # escaneo activo nunca corría `agents/verificacion.py`. Ahora se
        # aplica la misma verificación determinista (Exploit-DB offline) que
        # ya corre implícitamente en el escaneo pasivo dentro del grafo, ANTES
        # de normalizar para `_registrar_findings_con_ciclo_de_vida`.
        hallazgos_normalizados = []
        for hallazgo in hallazgos:
            hallazgo_verificado = verificar_un_hallazgo(hallazgo)
            hallazgos_normalizados.append(
                {
                    "tipo": hallazgo_verificado.get("name", "desconocido"),
                    # Bug real cerrado en una sesión anterior (ver
                    # tools/_shared.py::normalize_severity): estos hallazgos
                    # vienen de tools/zap_api.py::get_alerts() (formato de la
                    # API real de ZAP), que nunca trae `riskdesc`.
                    "severidad": normalize_severity(hallazgo_verificado, "zap"),
                    # Mismo bug de forma en el campo endpoint: get_alerts()
                    # devuelve una fila POR INSTANCIA con su propio `url` de
                    # nivel superior, nunca `instances`.
                    "endpoint": hallazgo_verificado.get("url")
                    or (hallazgo_verificado.get("instances") or [{}])[0].get("uri", ""),
                    "confirmado": bool(hallazgo_verificado["confirmado"]),
                    "raw_json": json.dumps(hallazgo_verificado),
                }
            )

        _registrar_findings_con_ciclo_de_vida(
            conn, tenant_id, scan_id, asset_id, hallazgos_normalizados, now
        )
        conn.commit()
    finally:
        conn.close()


@app.post("/scan/activo", response_model=ActiveScanAccepted, status_code=202)
def escaneo_activo_async(
    payload: ActiveScanRequest, user: UserContext = Depends(get_current_user)
) -> ActiveScanAccepted:
    if not payload.autorizacion_firmada:
        raise HTTPException(
            status_code=403,
            detail="Escaneo activo requiere autorizacion_firmada=true explícito en este request.",
        )

    scan_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        autorizado, motivo, asset = _asset_verificado_para_target(conn, user.tenant_id, payload.target_url)
        if not autorizado:
            raise HTTPException(status_code=403, detail=motivo)
        conn.execute(
            """
            INSERT INTO scans (id, tenant_id, asset_id, target, autorizacion_firmada, estado, trace_log_json)
            VALUES (?, ?, ?, ?, 1, 'corriendo', '[]')
            """,
            (scan_id, user.tenant_id, asset["id"] if asset else None, payload.target_url),
        )
        conn.commit()
    finally:
        conn.close()

    hilo = threading.Thread(
        target=_correr_escaneo_activo_en_background,
        args=(scan_id, user.tenant_id, asset["id"] if asset else None, payload),
        daemon=True,
    )
    hilo.start()

    return ActiveScanAccepted(scan_id=scan_id, estado="corriendo")


# ---------------------------------------------------------------------------
# Protección de marca / Anti-suplantación bajo demanda
# (openspec/changes/fase3a-antisuplantacion-y-scoring): dispara SOLO la rama
# anti-suplantación (`agents/antisuplantacion.py`, dnstwist+Sherlock+IA), la
# enriquece con scoring de infraestructura activa 0-4 + screenshot
# best-effort, y persiste los hallazgos vía el ciclo de vida existente de
# Fase 2a. Cero tabla/columna nueva: score/scoring/screenshot/ssl_not_before
# viajan dentro de `findings.raw_json["señal"]` (ver design.md, Data Flow).
# ---------------------------------------------------------------------------

VIGIA_SCREENSHOT_DIR = os.environ.get("VIGIA_SCREENSHOT_DIR", "data/screenshots")


def _enriquecer_dominios_clon(antisuplantacion_findings: list[dict], out_dir: str) -> None:
    """Enriquece cada señal `dominio_variante` con scoring + screenshot, mutando `señal` in place.

    `antisuplantacion_findings` es la salida cruda de
    `agents/antisuplantacion.py::node()` (`state["antisuplantacion_findings"]`
    -- una lista de `{"señales_crudas": [...], "analisis_claude": ...}`).
    `_extraer_findings_antisuplantacion` (más arriba) ya empaqueta cada
    `señal` completa dentro de `raw_json["señal"]` al persistir, así que
    agregar aquí las claves `scoring`/`score`/`ssl_not_before`/`screenshot`
    a la señal misma es suficiente para que viajen a `findings.raw_json`
    sin ninguna columna SQL nueva (ver design.md, Architecture Decisions).

    Solo se enriquecen señales `tipo == "dominio_variante"` -- un
    `perfil_red_social` no tiene un dominio del que hacer scoring de
    infraestructura ni una URL apta para un screenshot representativo del
    "sitio clon" (spec.md no lo exige para ese tipo).
    """
    for hallazgo in antisuplantacion_findings or []:
        for señal in hallazgo.get("señales_crudas") or []:
            if señal.get("tipo") != "dominio_variante":
                continue
            dominio = (señal.get("dominio") or "").strip()
            if not dominio:
                continue
            infra = puntuar_infra_lookalike(dominio)
            señal["scoring"] = {
                "dns_a": infra.dns_a,
                "mx": infra.mx,
                "ssl": infra.ssl,
                "http": infra.http,
            }
            señal["score"] = infra.score
            señal["ssl_not_before"] = infra.ssl_not_before
            señal["screenshot"] = capturar_screenshot(dominio, out_dir)


class AntisuplantacionRunIn(BaseModel):
    asset_id: str = Field(..., description="Asset tipo 'dominio', registrado y verificado (o exento) por este tenant")


class DominioClonOut(BaseModel):
    finding_id: str
    endpoint: str
    severidad: str
    estado: str
    score: int = 0
    scoring: dict = Field(default_factory=dict)
    screenshot: Optional[str] = None
    takedown: Optional[str] = None
    primera_vez_visto: Optional[str] = None


class AntisuplantacionRunOut(BaseModel):
    scan_id: str
    dominios: list[DominioClonOut] = Field(default_factory=list)
    perfiles: list[DominioClonOut] = Field(default_factory=list)


class AntisuplantacionKpiOut(BaseModel):
    tttd_dias: Optional[float] = None
    tttd_precision: str = "exacto"
    mttd_dias: Optional[float] = None
    mttd_precision: str = "aproximado"
    total_clones: int = 0
    clones_activos: int = 0


def _dominio_clon_out_de_row(d: dict) -> DominioClonOut:
    """Proyecta una fila `findings` (`tipo` `dominio_variante`/`perfil_red_social`)
    a `DominioClonOut`, leyendo scoring/screenshot/takedown desde
    `raw_json["señal"]`/`raw_json["analisis_claude"]` -- tolerante por
    diseño (mismo espíritu que `_detalle_desde_raw_json`): `raw_json`
    ausente/corrupto o de un finding sin las claves nuevas nunca rompe,
    solo devuelve defaults vacíos."""
    try:
        raw = json.loads(d.get("raw_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    señal = raw.get("señal") if isinstance(raw.get("señal"), dict) else {}
    scoring = señal.get("scoring") if isinstance(señal.get("scoring"), dict) else {}
    score = señal.get("score") if isinstance(señal.get("score"), int) else 0
    screenshot_path = señal.get("screenshot")
    screenshot_url = f"/antisuplantacion/dominios/{d['id']}/screenshot" if screenshot_path else None
    return DominioClonOut(
        finding_id=d["id"],
        endpoint=d["endpoint"],
        severidad=d["severidad"],
        estado=d["estado"],
        score=score,
        scoring=scoring,
        screenshot=screenshot_url,
        takedown=raw.get("analisis_claude"),
        primera_vez_visto=d.get("primera_vez_visto"),
    )


@app.post("/antisuplantacion", response_model=AntisuplantacionRunOut)
def ejecutar_antisuplantacion(
    payload: AntisuplantacionRunIn, user: UserContext = Depends(get_current_user)
) -> AntisuplantacionRunOut:
    """Dispara SOLO la rama anti-suplantación sobre un asset dominio ya
    verificado (o exento) del tenant -- ver design.md, Data Flow.

    A diferencia de `POST /scan` (que corre el pipeline completo con
    `antisuplantacion_habilitado=true`), esto llama
    `agents.antisuplantacion.node()` directo con un estado mínimo, sin
    recon/escaneo técnico/verificación/priorización/remediación -- más
    barato y permite disparar la protección de marca por sí sola.

    Síncrono a propósito (spec.md exige 200 con la lista de dominios ya
    resuelta en la misma respuesta, no un `scan_id` para hacer polling) --
    dnstwist+Sherlock bajo demanda tardan segundos, no los 20-180 minutos de
    un escaneo activo ZAP que sí justifica el patrón background de
    `POST /scan/activo`.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM assets WHERE id = ? AND tenant_id = ?",
            (payload.asset_id, user.tenant_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Activo no encontrado para este tenant.")
        asset = dict_from_row(row)

        if asset["tipo"] != "dominio":
            raise HTTPException(
                status_code=422,
                detail="La protección de marca solo aplica a activos tipo 'dominio'.",
            )
        if not asset_autorizado_para_escanear(asset["tipo"], asset["valor"], bool(asset["verificado"])):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"El activo '{asset['valor']}' (id={asset['id']}) está registrado pero NO "
                    "verificado. Verifícalo primero con POST /assets/{id}/verify."
                ),
            )

        estado_inicial = new_state(target=asset["valor"], antisuplantacion_habilitado=True)
        resultado_nodo = antisuplantacion_node(estado_inicial)
        señales_crudas = resultado_nodo.get("antisuplantacion_findings") or []

        _enriquecer_dominios_clon(señales_crudas, VIGIA_SCREENSHOT_DIR)

        scan_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO scans
                (id, tenant_id, asset_id, target, autorizacion_firmada, estado, completed_at)
            VALUES (?, ?, ?, ?, 1, 'completado', ?)
            """,
            (scan_id, user.tenant_id, asset["id"], asset["valor"], now),
        )

        filas = _extraer_findings_antisuplantacion(asset["valor"], señales_crudas)
        hallazgos_normalizados = [
            {
                "tipo": fila["tipo"],
                "severidad": fila["severidad"],
                "endpoint": fila["endpoint"],
                "confirmado": False,
                "raw_json": json.dumps(fila["raw_json"], default=str),
            }
            for fila in filas
        ]
        _registrar_findings_con_ciclo_de_vida(
            conn, user.tenant_id, scan_id, asset["id"], hallazgos_normalizados, now
        )
        conn.commit()

        # `_registrar_findings_con_ciclo_de_vida` no reasigna `scan_id` a un
        # hallazgo que ya existía de una corrida anterior (drift) -- por eso
        # la respuesta se arma buscando por (tenant, tipo, endpoint), no por
        # `scan_id`, igual que hace `GET /antisuplantacion/dominios` (scoped
        # a todo el tenant, no a un solo scan).
        dominios: list[DominioClonOut] = []
        perfiles: list[DominioClonOut] = []
        for fila in filas:
            fila_row = conn.execute(
                "SELECT * FROM findings WHERE tenant_id = ? AND tipo = ? AND endpoint = ?",
                (user.tenant_id, fila["tipo"], fila["endpoint"]),
            ).fetchone()
            if fila_row is None:
                continue
            proyeccion = _dominio_clon_out_de_row(dict_from_row(fila_row))
            if fila["tipo"] == "dominio_variante":
                dominios.append(proyeccion)
            else:
                perfiles.append(proyeccion)
    finally:
        conn.close()

    return AntisuplantacionRunOut(scan_id=scan_id, dominios=dominios, perfiles=perfiles)


@app.get("/antisuplantacion/dominios", response_model=list[DominioClonOut])
def listar_dominios_clon(user: UserContext = Depends(get_current_user)) -> list[DominioClonOut]:
    """Todos los dominios clon / perfiles falsos del tenant, con score,
    evidencia y borrador de takedown -- scoped a `tenant_id`, sin filtrar
    por scan (ver docstring de `ejecutar_antisuplantacion`: un hallazgo
    reincidente conserva su `scan_id` original)."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM findings
            WHERE tenant_id = ? AND tipo IN ('dominio_variante', 'perfil_red_social')
            ORDER BY created_at DESC
            """,
            (user.tenant_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_dominio_clon_out_de_row(dict_from_row(r)) for r in rows]


@app.get("/antisuplantacion/dominios/{finding_id}/screenshot")
def obtener_screenshot_clon(finding_id: str, user: UserContext = Depends(get_current_user)) -> FileResponse:
    """Sirve el screenshot binario de un dominio clon -- tenant-scoped, 404
    si el hallazgo no existe, es de otro tenant, o no tiene screenshot
    capturado (ej. Playwright no instalado, ver `capturar_screenshot`)."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM findings WHERE id = ? AND tenant_id = ?", (finding_id, user.tenant_id)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Hallazgo no encontrado")

    d = dict_from_row(row)
    try:
        raw = json.loads(d.get("raw_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        raw = {}
    señal = raw.get("señal") if isinstance(raw, dict) and isinstance(raw.get("señal"), dict) else {}
    ruta = señal.get("screenshot")
    if not ruta or not os.path.isfile(ruta):
        raise HTTPException(status_code=404, detail="Sin screenshot disponible para este hallazgo")
    return FileResponse(ruta, media_type="image/png")


@app.get("/antisuplantacion/kpis", response_model=AntisuplantacionKpiOut)
def obtener_kpis_antisuplantacion(user: UserContext = Depends(get_current_user)) -> AntisuplantacionKpiOut:
    """KPIs de marca: MTTD (best-effort, vía SSL `NotBefore`) y TTTD (exacto,
    reusa `finding_transiciones` de Fase 2a) -- mismo patrón de cálculo en
    Python que `GET /kpis` (Fase 2c), portable SQLite/Postgres sin
    `julianday()`/`EXTRACT`. Ver spec.md, Domain "KPIs de marca".
    """
    conn = get_conn()
    try:
        findings_rows = conn.execute(
            """
            SELECT * FROM findings
            WHERE tenant_id = ? AND tipo IN ('dominio_variante', 'perfil_red_social')
            """,
            (user.tenant_id,),
        ).fetchall()
        cierres_rows = conn.execute(
            """
            SELECT finding_id, MIN(created_at) AS cerrado_en
            FROM finding_transiciones
            WHERE tenant_id = ? AND estado_nuevo = 'cerrado'
            GROUP BY finding_id
            """,
            (user.tenant_id,),
        ).fetchall()
    finally:
        conn.close()

    primer_cierre = {r["finding_id"]: r["cerrado_en"] for r in cierres_rows}

    tttd_valores: list[float] = []
    mttd_valores: list[float] = []
    total_clones = 0
    clones_activos = 0

    for row in findings_rows:
        d = dict_from_row(row)
        total_clones += 1
        try:
            raw = json.loads(d.get("raw_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            raw = {}
        señal = raw.get("señal") if isinstance(raw, dict) and isinstance(raw.get("señal"), dict) else {}
        score = señal.get("score") if isinstance(señal.get("score"), int) else 0
        if score >= 2:
            clones_activos += 1

        cerrado_en = primer_cierre.get(d["id"])
        primera_vez_visto = d.get("primera_vez_visto")
        if cerrado_en and primera_vez_visto:
            try:
                delta_dias = (
                    _parse_dt_utc_naive(cerrado_en) - _parse_dt_utc_naive(primera_vez_visto)
                ).total_seconds() / 86400.0
                tttd_valores.append(max(delta_dias, 0.0))
            except (TypeError, ValueError):
                pass

        ssl_not_before = señal.get("ssl_not_before")
        if ssl_not_before and primera_vez_visto:
            try:
                delta_mttd = (
                    _parse_dt_utc_naive(primera_vez_visto) - _parse_dt_utc_naive(ssl_not_before)
                ).total_seconds() / 86400.0
                mttd_valores.append(max(delta_mttd, 0.0))
            except (TypeError, ValueError):
                pass

    tttd_dias = (sum(tttd_valores) / len(tttd_valores)) if tttd_valores else None
    mttd_dias = (sum(mttd_valores) / len(mttd_valores)) if mttd_valores else None

    return AntisuplantacionKpiOut(
        tttd_dias=tttd_dias,
        mttd_dias=mttd_dias,
        total_clones=total_clones,
        clones_activos=clones_activos,
    )


class ScanDetail(BaseModel):
    id: str
    target: str
    estado: str
    autorizacion_firmada: bool
    reporte_final: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    findings: list[FindingOut] = Field(default_factory=list)


@app.get("/scans/{scan_id}", response_model=ScanDetail)
def detalle_scan(scan_id: str, user: UserContext = Depends(get_current_user)) -> ScanDetail:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM scans WHERE id = ? AND tenant_id = ?", (scan_id, user.tenant_id)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Scan no encontrado")
        hallazgos = conn.execute(
            "SELECT * FROM findings WHERE scan_id = ? ORDER BY created_at DESC", (scan_id,)
        ).fetchall()
    finally:
        conn.close()

    return ScanDetail(
        id=row["id"],
        target=row["target"],
        estado=row["estado"],
        autorizacion_firmada=bool(row["autorizacion_firmada"]),
        reporte_final=row["reporte_final"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
        findings=[_finding_out_de_row(dict_from_row(f)) for f in hallazgos],
    )


@app.get("/scans/{scan_id}/report/download")
def reporte_scan_download(
    scan_id: str, formato: str = "pdf", user: UserContext = Depends(get_current_user)
) -> Response:
    """Descarga el reporte técnico de UN scan (`scans.reporte_final`, texto ya
    generado por `agents/reporteria.py` dentro del grafo) como PDF o DOCX.

    Distinto de `/reports/cumplimiento/download`: este es el reporte de una
    sola corrida (recon/escaneo/verificación/priorización/remediación), no
    el acumulado normativo de todo el historial del tenant.
    """
    if formato not in ("pdf", "docx"):
        raise HTTPException(status_code=422, detail="formato debe ser 'pdf' o 'docx'")

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM scans WHERE id = ? AND tenant_id = ?", (scan_id, user.tenant_id)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Scan no encontrado")

    reporte = row["reporte_final"] or (
        "Este escaneo todavía no tiene un reporte final generado "
        f"(estado actual: {row['estado']})."
    )
    titulo = f"Reporte técnico de escaneo — {row['target']}"

    if formato == "pdf":
        contenido = markdown_to_pdf_bytes(reporte, titulo)
        media_type = "application/pdf"
        filename = f"reporte-scan-{scan_id[:8]}.pdf"
    else:
        contenido = markdown_to_docx_bytes(reporte, titulo)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = f"reporte-scan-{scan_id[:8]}.docx"

    return Response(
        content=contenido,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Invitaciones de equipo — item transversal de HANDOFF.md ("invitar más
# usuarios al mismo tenant"). `role` ya admitía 'admin'/'member' en el schema
# desde el principio, pero no existía ningún endpoint ni flujo de UI para
# usarlo — hoy solo 'owner' funcionaba en la práctica (el usuario que
# registra el tenant). Diseño deliberadamente simple: sin envío de email
# (no hay proveedor de correo configurado en el proyecto todavía), el
# owner/admin comparte el link de invitación manualmente (copiar/pegar).
# ---------------------------------------------------------------------------


class InvitationIn(BaseModel):
    email: str
    role: str = Field(default="member", description="'admin' o 'member'")


class InvitationOut(BaseModel):
    id: str
    email: str
    role: str
    estado: str
    token: str
    created_at: str
    expires_at: Optional[str] = None


class MemberOut(BaseModel):
    id: str
    email: str
    role: str
    created_at: str


class InvitationPreview(BaseModel):
    valido: bool
    email: Optional[str] = None
    tenant_nombre: Optional[str] = None
    role: Optional[str] = None
    motivo: Optional[str] = None


@app.post("/tenant/invitations", response_model=InvitationOut, status_code=201)
def crear_invitacion(
    payload: InvitationIn, user: UserContext = Depends(require_role("owner", "admin"))
) -> InvitationOut:
    if payload.role not in ("admin", "member"):
        raise HTTPException(status_code=422, detail="role debe ser 'admin' o 'member'")

    conn = get_conn()
    try:
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (payload.email,)).fetchone():
            raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese email")
        if conn.execute(
            "SELECT 1 FROM invitations WHERE tenant_id = ? AND email = ? AND estado = 'pendiente'",
            (user.tenant_id, payload.email),
        ).fetchone():
            raise HTTPException(status_code=409, detail="Ya hay una invitación pendiente para ese email")

        invitation_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(24)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        conn.execute(
            """
            INSERT INTO invitations (id, tenant_id, email, token, role, invited_by, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (invitation_id, user.tenant_id, payload.email, token, payload.role, user.user_id, expires_at),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM invitations WHERE id = ?", (invitation_id,)).fetchone()
    finally:
        conn.close()

    return InvitationOut(**dict_from_row(row))


@app.get("/tenant/invitations", response_model=list[InvitationOut])
def listar_invitaciones(user: UserContext = Depends(require_role("owner", "admin"))) -> list[InvitationOut]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM invitations WHERE tenant_id = ? ORDER BY created_at DESC", (user.tenant_id,)
        ).fetchall()
    finally:
        conn.close()
    return [InvitationOut(**dict_from_row(r)) for r in rows]


@app.delete("/tenant/invitations/{invitation_id}", status_code=204)
def revocar_invitacion(
    invitation_id: str, user: UserContext = Depends(require_role("owner", "admin"))
) -> None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM invitations WHERE id = ? AND tenant_id = ?", (invitation_id, user.tenant_id)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Invitación no encontrada")
        conn.execute("UPDATE invitations SET estado = 'revocada' WHERE id = ?", (invitation_id,))
        conn.commit()
    finally:
        conn.close()


@app.get("/tenant/members", response_model=list[MemberOut])
def listar_miembros(user: UserContext = Depends(get_current_user)) -> list[MemberOut]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, email, role, created_at FROM users WHERE tenant_id = ? ORDER BY created_at ASC",
            (user.tenant_id,),
        ).fetchall()
    finally:
        conn.close()
    return [MemberOut(**dict_from_row(r)) for r in rows]


@app.get("/tenant/invitations/preview/{token}", response_model=InvitationPreview)
def previsualizar_invitacion(token: str) -> InvitationPreview:
    """Endpoint público (sin auth): quien recibió el link todavía no tiene cuenta.

    Usado por el frontend (Login.tsx) para mostrar "te invitaron a unirte a
    <negocio>" antes de que la persona invitada llene el formulario de
    registro. No expone nada del tenant más allá de su nombre.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT i.*, t.name AS tenant_nombre
            FROM invitations i JOIN tenants t ON t.id = i.tenant_id
            WHERE i.token = ?
            """,
            (token,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return InvitationPreview(valido=False, motivo="Invitación no encontrada")
    d = dict_from_row(row)
    if d["estado"] != "pendiente":
        return InvitationPreview(valido=False, motivo="Esta invitación ya fue usada o revocada")
    if d["expires_at"] and datetime.fromisoformat(d["expires_at"]) < datetime.now(timezone.utc):
        return InvitationPreview(valido=False, motivo="Esta invitación expiró")
    return InvitationPreview(
        valido=True, email=d["email"], tenant_nombre=d["tenant_nombre"], role=d["role"]
    )


# ---------------------------------------------------------------------------
# Servir el build de producción del frontend (React/Vite) desde el mismo
# proceso/contenedor -- objetivo: un solo servicio deployable en Railway/Render
# en vez de backend + frontend como dos servicios separados (más setup, más
# costo, ver docs/despliegue.md). Todo este bloque se registra AL FINAL del
# archivo, después de cada `@app.get/post/delete` de arriba, a propósito: en
# Starlette/FastAPI la primera ruta registrada que matchea un request gana, y
# el catch-all de abajo (`/{full_path:path}`) matchea *cualquier* string --
# si se registrara antes, le robaría requests a las rutas reales de la API.
# Registrado último, nunca compite con ellas.
#
# Solo se activa si `frontend/dist/` existe de verdad (un build real ya
# corrido) -- así el flujo de dev local sin build presente
# (`scripts/demo.ps1`, dos dev servers separados por HANDOFF.md) sigue
# funcionando exactamente igual que antes: sin `frontend/dist/`, ninguna ruta
# nueva se registra y el comportamiento del proceso no cambia en nada.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# Primer segmento de cada ruta de API real declarada arriba en este archivo
# (auth/*, assets, assets/{id}/verify, scans/*, findings, reports/*,
# tenant/*, health, me, scan, scan/activo) -- usado únicamente para que el
# catch-all de más abajo pueda distinguir "esto se parece a una llamada de
# API con un path equivocado" (debe seguir devolviendo 404 JSON real) de
# "esto es una ruta de cliente de React Router" (debe servir index.html).
# No reemplaza el ruteo real: cualquier ruta de arriba que sí matchea exacto
# (ej. GET /health, POST /auth/login) ya fue resuelta por FastAPI antes de
# que un request llegue hasta aquí -- esta lista solo importa para los
# sub-paths que NO matchean ninguna ruta real (ej. GET /auth/typo-de-ruta).
_API_PATH_PREFIXES = {
    "auth",
    "me",
    "assets",
    "scans",
    "findings",
    "reports",
    "tenant",
    "health",
    "scan",
    "docs",
    "redoc",
    "openapi.json",
}

if FRONTEND_DIST.is_dir():
    _assets_dir = FRONTEND_DIST / "static-assets"
    if _assets_dir.is_dir():
        # Nombre de carpeta distinto de "/assets" a propósito (ver
        # frontend/vite.config.ts, build.assetsDir) -- "/assets" ya es una
        # ruta real de la API (CRUD de activos del tenant, GET/POST /assets
        # arriba en este archivo). Montar StaticFiles ahí habría creado una
        # colisión real de nombres entre los JS/CSS con hash de Vite y el
        # endpoint de activos, no solo teórica.
        app.mount("/static-assets", StaticFiles(directory=str(_assets_dir)), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def servir_frontend(full_path: str) -> Response:
        """Catch-all: sirve el SPA de React para cualquier GET que no sea la API.

        Necesario para que las rutas de React Router (ej. `/login`,
        `/dashboard`) funcionen con un hard refresh o un link directo, no
        solo con navegación client-side -- sin esto, `GET /dashboard` directo
        contra el servidor (sin pasar primero por `/` y el router de React)
        devolvería 404 en vez de la SPA real.

        Tres casos, en este orden:
        1. `full_path` empieza con un prefijo de la API real (`_API_PATH_PREFIXES`)
           pero no matcheó ninguna ruta real arriba (ya se intentó antes de
           llegar aquí) -- 404 JSON real, nunca `index.html`. Evita que un
           typo de ruta de API se sirva silenciosamente como HTML.
        2. `full_path` corresponde a un archivo real dentro de `frontend/dist/`
           (ej. `favicon.svg`, o un asset bajo `static-assets/` si el mount de
           arriba no lo resolvió por algún motivo) -- se sirve ese archivo tal
           cual.
        3. Cualquier otro GET (incluyendo `/`, `/login`, `/dashboard`, etc.)
           -- se sirve `index.html`, dejando que React Router decida la vista
           en el cliente.
        """
        primer_segmento = full_path.split("/", 1)[0]
        if primer_segmento in _API_PATH_PREFIXES:
            raise HTTPException(status_code=404, detail="Not Found")

        # Resuelve dentro de FRONTEND_DIST y confirma que el resultado sigue
        # siendo un descendiente real de ese directorio -- sin esto, un
        # `full_path` malicioso tipo `../../etc/passwd` podría escapar del
        # directorio del build (path traversal real, no hipotético).
        candidato = (FRONTEND_DIST / full_path).resolve()
        if (
            full_path
            and candidato.is_file()
            and candidato.is_relative_to(FRONTEND_DIST)
        ):
            return FileResponse(candidato)

        return FileResponse(FRONTEND_DIST / "index.html")
