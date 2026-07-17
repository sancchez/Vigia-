"""Autenticación JWT + bcrypt propia (sin proveedor externo).

Patrón adaptado de metads-100adsproto/backend/auth/jwt_auth.py — el único
mecanismo de auth realmente probado (no un stub) en el ecosistema. Aquí es
síncrono en vez de async (sqlite3, no asyncpg) para no forzar un salto a
FastAPI async en todo `api/main.py` de una sola vez.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from db.connection import get_conn

security = HTTPBearer()

_JWT_ALGO = "HS256"
_TOKEN_TTL = timedelta(days=7)


def _jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        # Fallback SOLO para desarrollo local sin .env configurado — nunca usar
        # este valor en un despliegue real. Configura JWT_SECRET en .env.
        secret = "dev-only-insecure-secret-configura-JWT_SECRET-en-.env"
    return secret


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    to_encode["exp"] = datetime.now(timezone.utc) + _TOKEN_TTL
    return jwt.encode(to_encode, _jwt_secret(), algorithm=_JWT_ALGO)


class UserContext(BaseModel):
    user_id: str
    tenant_id: str
    email: str
    role: str
    plan: str


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> UserContext:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[_JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")

    # Camino rápido: el token ya trae todos los claims (login reciente).
    if all(payload.get(k) for k in ("email", "role", "plan", "tenant_id")):
        return UserContext(
            user_id=user_id,
            tenant_id=payload["tenant_id"],
            email=payload["email"],
            role=payload["role"],
            plan=payload["plan"],
        )

    # Fallback: token viejo con menos claims — se completa consultando la DB.
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT u.id AS user_id, u.tenant_id, u.email, u.role, t.plan
            FROM users u JOIN tenants t ON t.id = u.tenant_id
            WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    return UserContext(
        user_id=row["user_id"],
        tenant_id=row["tenant_id"],
        email=row["email"],
        role=row["role"],
        plan=row["plan"],
    )


def require_role(*roles: str):
    def role_checker(user: UserContext = Depends(get_current_user)) -> UserContext:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Permisos insuficientes")
        return user

    return role_checker
