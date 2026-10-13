"""Cuentas de usuario y sesiones.

- Contraseñas: hash scrypt (biblioteca estándar de Python) con sal aleatoria.
- Sesiones: token aleatorio en una cookie httpOnly; en la base solo se
  guarda su hash SHA-256, así una copia de la base no permite entrar.
- Intentos fallidos de inicio de sesión limitados por IP y usuario.
"""
import hashlib
import hmac
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Note, SessionLocal, User, UserSession

COOKIE_NAME = "notas_sesion"
SESSION_DAYS = 30
RENEW_WHEN_LESS_THAN = timedelta(days=15)
# En red local se usa http; si algún día se sirve por https, poner COOKIE_SECURE=true
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
ALLOW_SIGNUP = os.environ.get("ALLOW_SIGNUP", "true").lower() == "true"

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,30}$")
MIN_PASSWORD = 8

# ---------- Contraseñas ----------
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32,
        )
        return hmac.compare_digest(candidate.hex(), digest)
    except (ValueError, TypeError):
        return False


# Para usuarios inexistentes se verifica igual contra este hash, así el
# tiempo de respuesta no revela qué nombres de usuario existen.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


# ---------- Límite de intentos ----------
MAX_FAILS = 5
FAIL_WINDOW = 10 * 60  # segundos
_fails: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    return request.headers.get("X-Real-IP") or (request.client.host if request.client else "?")


def check_rate_limit(request: Request, username: str) -> None:
    key = f"{_client_ip(request)}|{username}"
    now = time.monotonic()
    attempts = _fails[key]
    while attempts and now - attempts[0] > FAIL_WINDOW:
        attempts.popleft()
    if len(attempts) >= MAX_FAILS:
        raise HTTPException(429, "Demasiados intentos fallidos. Espera unos minutos y vuelve a probar.")


def record_failure(request: Request, username: str) -> None:
    _fails[f"{_client_ip(request)}|{username}"].append(time.monotonic())


def clear_failures(request: Request, username: str) -> None:
    _fails.pop(f"{_client_ip(request)}|{username}", None)


# ---------- Validación ----------
def normalize_username(raw: str) -> str:
    return raw.strip().lower()


def validate_new_account(username: str, password: str) -> None:
    if not USERNAME_RE.match(username):
        raise HTTPException(
            422, "El usuario debe tener entre 3 y 30 caracteres: letras sin tilde, números, punto, guion o guion bajo.",
        )
    if len(password) < MIN_PASSWORD:
        raise HTTPException(422, f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres.")
    if len(password) > 200:
        raise HTTPException(422, "La contraseña es demasiado larga.")


# ---------- Sesiones ----------
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=COOKIE_SECURE, path="/",
    )


def start_session(db: Session, user: User, request: Request, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    db.add(UserSession(
        user_id=user.id,
        token_hash=_token_hash(token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS),
        user_agent=(request.headers.get("User-Agent") or "")[:200] or None,
    ))
    db.commit()
    _set_cookie(response, token)


def end_session(db: Session, request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        found = db.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
        if found:
            db.delete(found)
            db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


def session_user(db: Session, request: Request, response: Response | None = None) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    found = db.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    now = datetime.now(timezone.utc)
    if not found or found.expires_at <= now:
        return None
    # Sesión deslizante: si se usa la app, la sesión se renueva sola
    if response is not None and found.expires_at - now < RENEW_WHEN_LESS_THAN:
        found.expires_at = now + timedelta(days=SESSION_DAYS)
        db.commit()
        _set_cookie(response, token)
    return found.user


def get_db():
    with SessionLocal() as db:
        yield db


def current_user(request: Request, response: Response, db: Session = Depends(get_db)) -> User:
    user = session_user(db, request, response)
    if user is None:
        raise HTTPException(401, "Inicia sesión para continuar.")
    return user


def claim_orphan_notes(db: Session, user: User) -> int:
    """La primera cuenta recibe las notas creadas antes de que existieran usuarios."""
    result = db.execute(update(Note).where(Note.user_id.is_(None)).values(user_id=user.id))
    return result.rowcount or 0
