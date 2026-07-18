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
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from auth.jwt_auth import (
    UserContext,
    create_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
)
from api.scheduler import start_scheduler, stop_scheduler
from db.connection import dict_from_row, get_conn
from orchestrator.graph import compile_graph
from orchestrator.state import new_state

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
    yield
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
    """Registro de un tenant nuevo (una pyme cliente) + su primer usuario (owner)."""

    nombre_negocio: str = Field(..., min_length=2, description="Nombre del negocio, ej. 'Mi Pyme S.A.S.'")
    email: str
    password: str = Field(..., min_length=8)


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
    id: str
    tipo: str
    valor: str
    notas: str
    is_active: bool
    created_at: str


class ScanHistoryItem(BaseModel):
    id: str
    target: str
    estado: str
    autorizacion_firmada: bool
    total_hallazgos: int
    created_at: str
    completed_at: Optional[str] = None


class FindingOut(BaseModel):
    id: str
    scan_id: str
    tipo: str
    severidad: str
    endpoint: str
    confirmado: bool
    created_at: str


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
    """Crea un tenant nuevo con su primer usuario (role=owner) y devuelve un token."""
    conn = get_conn()
    try:
        existing = conn.execute("SELECT 1 FROM users WHERE email = ?", (payload.email,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese email")

        tenant_id = str(uuid.uuid4())
        slug = _unique_slug(conn, _slugify(payload.nombre_negocio))
        user_id = str(uuid.uuid4())

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
        {"sub": user_id, "tenant_id": tenant_id, "email": payload.email, "role": "owner", "plan": "trial"}
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


@app.post("/assets", response_model=AssetOut)
def crear_asset(payload: AssetIn, user: UserContext = Depends(get_current_user)) -> AssetOut:
    if payload.tipo not in ("dominio", "app", "ip"):
        raise HTTPException(status_code=422, detail="tipo debe ser 'dominio', 'app' o 'ip'")
    asset_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        try:
            conn.execute(
                "INSERT INTO assets (id, tenant_id, tipo, valor, notas) VALUES (?, ?, ?, ?, ?)",
                (asset_id, user.tenant_id, payload.tipo, payload.valor, payload.notas),
            )
            conn.commit()
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"Ese activo ya está registrado: {exc}")
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    finally:
        conn.close()
    d = dict_from_row(row)
    return AssetOut(**{**d, "is_active": bool(d["is_active"])})


@app.get("/assets", response_model=list[AssetOut])
def listar_assets(user: UserContext = Depends(get_current_user)) -> list[AssetOut]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM assets WHERE tenant_id = ? ORDER BY created_at DESC", (user.tenant_id,)
        ).fetchall()
    finally:
        conn.close()
    return [AssetOut(**{**dict_from_row(r), "is_active": bool(r["is_active"])}) for r in rows]


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


@app.get("/findings", response_model=list[FindingOut])
def listar_findings(user: UserContext = Depends(get_current_user)) -> list[FindingOut]:
    """Todos los hallazgos del tenant, más recientes primero — usado por el panel para el resumen de riesgo."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM findings WHERE tenant_id = ? ORDER BY created_at DESC", (user.tenant_id,)
        ).fetchall()
    finally:
        conn.close()
    return [
        FindingOut(
            id=r["id"],
            scan_id=r["scan_id"],
            tipo=r["tipo"],
            severidad=r["severidad"],
            endpoint=r["endpoint"],
            confirmado=bool(r["confirmado"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]


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


@app.post("/scan", response_model=ScanResponse)
def scan(payload: ScanRequest, user: UserContext = Depends(get_current_user)) -> ScanResponse:
    """Dispara el pipeline completo (`orchestrator.graph`) para `target`, para el tenant autenticado.

    Esta función NO decide nada sobre autorización: arma el `PipelineState`
    inicial exactamente con lo que llegó en el request (forma validada por
    Pydantic) y deja que el grafo compilado (`_get_graph().invoke(...)`)
    tome todas las decisiones de flujo, incluyendo el bloqueo determinista
    de Escaneo Activo cuando `autorizacion_firmada` no es `true`. Requerir
    `user` (JWT válido) es una puerta DISTINTA de la de autorización de
    escaneo — aquí solo se exige "eres un tenant registrado", no "tienes
    permiso legal para escanear este target" (eso lo sigue decidiendo el
    grafo, sección 8.1 del plan).
    """
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
                (id, tenant_id, target, autorizacion_firmada, estado, reporte_final, trace_log_json, completed_at)
            VALUES (?, ?, ?, ?, 'completado', ?, ?, ?)
            """,
            (
                scan_id,
                user.tenant_id,
                estado_final.get("target", payload.target),
                int(bool(estado_final.get("autorizacion_firmada", payload.autorizacion_firmada))),
                estado_final.get("reporte_final"),
                json.dumps(estado_final.get("trace_log") or []),
                now,
            ),
        )
        for hallazgo in verified:
            conn.execute(
                """
                INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    scan_id,
                    user.tenant_id,
                    hallazgo.get("tipo") or hallazgo.get("type") or "desconocido",
                    hallazgo.get("severidad") or hallazgo.get("severity") or "info",
                    hallazgo.get("endpoint") or hallazgo.get("objetivo") or "",
                    int(bool(hallazgo.get("confirmado"))),
                    json.dumps(hallazgo),
                ),
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
