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
from agents.cumplimiento import generar_reporte_cumplimiento
from api.certstream_listener import start_certstream_listener, stop_certstream_listener
from api.scheduler import start_scheduler, stop_scheduler
from db.connection import dict_from_row, get_conn
from orchestrator.graph import compile_graph
from orchestrator.state import new_state
from tools._shared import ToolExecutionError, normalize_severity
from tools.antisuplantacion import registrable_domain
from tools.asset_verification import (
    METODOS_VALIDOS,
    asset_autorizado_para_escanear,
    es_target_exento_de_verificacion,
    generar_token as generar_token_verificacion,
    instrucciones_verificacion,
    verificar_asset,
)
from tools.report_export import markdown_to_docx_bytes, markdown_to_pdf_bytes
from tools.zap_api import run_checkpointed_active_scan
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
        autorizado, motivo, _asset = _asset_verificado_para_target(conn_check, user.tenant_id, payload.target)
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
                    # Bug real cerrado esta sesión (ver tools/_shared.py::normalize_severity):
                    # `hallazgo.get("severidad")`/`("severity")` NUNCA existen a nivel
                    # superior -- `agents/verificacion.py` solo agrega metadata de
                    # verificación sobre `{"objetivo","herramienta","raw"}`
                    # (`agents/escaneo.py`), la severidad real vive dentro de `raw`
                    # con vocabulario propio de cada herramienta. Este `.get()` viejo
                    # siempre caía en "info" -- causa raíz confirmada de que
                    # findings.severidad valiera "info" para ~99% de las filas reales.
                    normalize_severity(hallazgo.get("raw"), hallazgo.get("herramienta", "")),
                    hallazgo.get("endpoint") or hallazgo.get("objetivo") or "",
                    int(bool(hallazgo.get("confirmado"))),
                    json.dumps(hallazgo),
                ),
            )

        # Cierra la brecha documentada en docs/cumplimiento.md / HANDOFF.md
        # (Item 6): las señales reales de dnstwist/Sherlock bajo demanda
        # ahora también se persisten, colgadas del mismo scan_id real.
        antisuplantacion_rows = _extraer_findings_antisuplantacion(
            estado_final.get("target", payload.target),
            estado_final.get("antisuplantacion_findings") or [],
        )
        for fila in antisuplantacion_rows:
            conn.execute(
                """
                INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    str(uuid.uuid4()),
                    scan_id,
                    user.tenant_id,
                    fila["tipo"],
                    fila["severidad"],
                    fila["endpoint"],
                    json.dumps(fila["raw_json"], default=str),
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
    scan_id: str, tenant_id: str, payload: ActiveScanRequest
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
        for hallazgo in hallazgos:
            conn.execute(
                """
                INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    str(uuid.uuid4()),
                    scan_id,
                    tenant_id,
                    hallazgo.get("name", "desconocido"),
                    # Bug real cerrado esta sesión (ver tools/_shared.py::normalize_severity):
                    # estos hallazgos vienen de tools/zap_api.py::get_alerts()
                    # (`core/view/alerts/`, formato de la API real de ZAP), que
                    # NUNCA trae `riskdesc` -- solo `run_zap_baseline`/
                    # `run_zap_active_scan` (tools/scan.py, reporte JSON de
                    # zap-baseline.py/zap-full-scan.py) traen esa clave. El
                    # `.get("riskdesc", "info")` de antes caía siempre al default
                    # literal para el 100% de estos hallazgos (1280/1280
                    # verificado en ciberseguridad.db).
                    normalize_severity(hallazgo, "zap"),
                    # Mismo bug de forma, en el campo endpoint: get_alerts()
                    # devuelve una fila POR INSTANCIA con su propio `url` de
                    # nivel superior -- nunca trae `instances` (eso es del
                    # formato de reporte JSON, no de la API). El `.get("instances")`
                    # de antes también caía siempre a "" para estos hallazgos
                    # (confirmado: 1280/1280 con endpoint vacío en la DB real).
                    hallazgo.get("url") or (hallazgo.get("instances") or [{}])[0].get("uri", ""),
                    json.dumps(hallazgo),
                ),
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
        autorizado, motivo, _asset = _asset_verificado_para_target(conn, user.tenant_id, payload.target_url)
        if not autorizado:
            raise HTTPException(status_code=403, detail=motivo)
        conn.execute(
            """
            INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado, trace_log_json)
            VALUES (?, ?, ?, 1, 'corriendo', '[]')
            """,
            (scan_id, user.tenant_id, payload.target_url),
        )
        conn.commit()
    finally:
        conn.close()

    hilo = threading.Thread(
        target=_correr_escaneo_activo_en_background,
        args=(scan_id, user.tenant_id, payload),
        daemon=True,
    )
    hilo.start()

    return ActiveScanAccepted(scan_id=scan_id, estado="corriendo")


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
        findings=[
            FindingOut(
                id=f["id"],
                scan_id=f["scan_id"],
                tipo=f["tipo"],
                severidad=f["severidad"],
                endpoint=f["endpoint"],
                confirmado=bool(f["confirmado"]),
                created_at=f["created_at"],
            )
            for f in hallazgos
        ],
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
