"""Operaciones de administración de cuentas.

Estas funciones no dependen de la terminal ni de la web: reciben una
sesión de base de datos y devuelven datos simples. Hoy las usa el comando
`python -m app.admin`; una futura vista de administrador puede usarlas
desde endpoints de la API sin repetir lógica.

Los errores esperables (usuario inexistente, contraseña inválida...) se
lanzan como AdminError con un mensaje listo para mostrar.
"""
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from . import auth
from .models import Note, Theme, User, UserSession


class AdminError(Exception):
    """Error de una operación de administración, con mensaje para el usuario."""

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.message = message
        # "not_found" | "conflict" | "invalid": permite traducirlo a 404/409/422 en una API
        self.code = code


@dataclass
class UserSummary:
    id: int
    username: str
    created_at: datetime
    notes_active: int
    notes_done: int
    sessions: int               # sesiones abiertas (dispositivos)
    last_login: datetime | None  # inicio de sesión más reciente aún abierto
    theme: str | None = None     # clave del tema asignado

    @property
    def notes_total(self) -> int:
        return self.notes_active + self.notes_done


@dataclass
class ThemeSummary:
    id: int
    clave: str
    nombre: str
    descripcion: str | None
    activo: bool
    users: int  # cuántos usuarios lo tienen asignado


@dataclass
class SessionInfo:
    id: int
    created_at: datetime
    expires_at: datetime
    device: str | None


# ---------- Consultas ----------

def list_users(db: Session) -> list[UserSummary]:
    now = datetime.now(timezone.utc)
    notes = (
        select(
            Note.user_id,
            func.count(case((Note.completed_at.is_(None), 1))).label("active"),
            func.count(case((Note.completed_at.is_not(None), 1))).label("done"),
        )
        .group_by(Note.user_id)
        .subquery()
    )
    sessions = (
        select(
            UserSession.user_id,
            func.count().label("open"),
            func.max(UserSession.created_at).label("last_login"),
        )
        .where(UserSession.expires_at > now)
        .group_by(UserSession.user_id)
        .subquery()
    )
    rows = db.execute(
        select(
            User.id, User.username, User.created_at,
            func.coalesce(notes.c.active, 0), func.coalesce(notes.c.done, 0),
            func.coalesce(sessions.c.open, 0), sessions.c.last_login, Theme.clave,
        )
        .outerjoin(notes, notes.c.user_id == User.id)
        .outerjoin(sessions, sessions.c.user_id == User.id)
        .outerjoin(Theme, Theme.id == User.theme_id)
        .order_by(User.id)
    ).all()
    return [UserSummary(*row) for row in rows]


def get_user(db: Session, username: str) -> User:
    user = db.scalar(select(User).where(User.username == auth.normalize_username(username)))
    if user is None:
        raise AdminError(f'No existe el usuario "{username}".', "not_found")
    return user


def list_sessions(db: Session, username: str) -> list[SessionInfo]:
    user = get_user(db, username)
    rows = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user.id, UserSession.expires_at > datetime.now(timezone.utc))
        .order_by(UserSession.created_at.desc())
    ).all()
    return [SessionInfo(s.id, s.created_at, s.expires_at, s.user_agent) for s in rows]


# ---------- Cambios ----------

def generate_password() -> str:
    """Contraseña temporal fácil de dictar: 3 grupos de 4 caracteres sin ambiguos (0/O, 1/l)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))


def create_user(db: Session, username: str, password: str) -> User:
    """Crea una cuenta con las mismas reglas que el registro de la app.
    No hace commit: quien llama decide cuándo confirmar."""
    name = auth.normalize_username(username)
    try:
        auth.validate_new_account(name, password)
    except auth.HTTPException as e:
        raise AdminError(e.detail) from None
    if db.scalar(select(User).where(User.username == name)):
        raise AdminError("Ese nombre de usuario ya existe.", "conflict")
    user = User(username=name, password_hash=auth.hash_password(password))
    db.add(user)
    db.flush()
    return user


def end_sessions(db: Session, username: str) -> int:
    """Cierra todas las sesiones del usuario: deberá iniciar sesión de nuevo en cada dispositivo."""
    user = get_user(db, username)
    result = db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
    return result.rowcount or 0


def reset_password(db: Session, username: str, new_password: str | None = None) -> tuple[str, int]:
    """Cambia la contraseña (o genera una temporal) y cierra todas sus sesiones.
    Devuelve la contraseña nueva y cuántas sesiones se cerraron."""
    user = get_user(db, username)
    password = new_password or generate_password()
    try:
        auth.validate_new_account(user.username, password)
    except auth.HTTPException as e:
        raise AdminError(e.detail) from None
    user.password_hash = auth.hash_password(password)
    closed = db.execute(delete(UserSession).where(UserSession.user_id == user.id)).rowcount or 0
    db.commit()
    return password, closed


def delete_user(db: Session, username: str) -> int:
    """Borra la cuenta, sus sesiones y TODAS sus notas. Devuelve cuántas notas se borraron."""
    user = get_user(db, username)
    notes = db.scalar(select(func.count()).select_from(Note).where(Note.user_id == user.id)) or 0
    db.delete(user)  # las sesiones y notas se borran en cascada en la base
    db.commit()
    return notes


# ---------- Temas visuales ----------
# El aspecto de cada tema está en frontend/public/temas.css, en el bloque
# body[data-tema="<clave>"]. Aquí solo se maneja el catálogo y la asignación.

THEME_KEY_RE = re.compile(r"^[a-z0-9-]{2,30}$")


def list_themes(db: Session) -> list[ThemeSummary]:
    users = select(User.theme_id, func.count().label("n")).group_by(User.theme_id).subquery()
    rows = db.execute(
        select(Theme.id, Theme.clave, Theme.nombre, Theme.descripcion, Theme.activo, func.coalesce(users.c.n, 0))
        .outerjoin(users, users.c.theme_id == Theme.id)
        .order_by(Theme.nombre)
    ).all()
    return [ThemeSummary(*row) for row in rows]


def get_theme(db: Session, clave: str) -> Theme:
    theme = db.scalar(select(Theme).where(Theme.clave == clave.strip().lower()))
    if theme is None:
        raise AdminError(f'No existe el tema "{clave}". Revisa la lista con: temas', "not_found")
    return theme


def create_theme(db: Session, clave: str, nombre: str, descripcion: str | None = None) -> Theme:
    """Registra un tema en el catálogo. Su CSS debe existir en temas.css con la misma clave."""
    clave = clave.strip().lower()
    if not THEME_KEY_RE.match(clave):
        raise AdminError("La clave del tema debe tener de 2 a 30 caracteres: letras minúsculas, números o guiones.")
    if not nombre.strip():
        raise AdminError("Escribe un nombre para el tema.")
    if db.scalar(select(Theme).where(Theme.clave == clave)):
        raise AdminError(f'Ya existe un tema con la clave "{clave}".', "conflict")
    theme = Theme(clave=clave, nombre=nombre.strip()[:60], descripcion=(descripcion or "").strip() or None)
    db.add(theme)
    db.commit()
    return theme


def set_theme_active(db: Session, clave: str, active: bool) -> Theme:
    """Un tema desactivado se conserva asignado, pero sus usuarios ven el diseño por defecto."""
    theme = get_theme(db, clave)
    theme.activo = active
    db.commit()
    return theme


def assign_theme(db: Session, username: str, clave: str | None) -> User:
    """Asigna un tema al usuario, o lo quita con clave=None. Se ve al recargar la app."""
    user = get_user(db, username)
    if clave is None:
        user.theme_id = None
    else:
        theme = get_theme(db, clave)
        if not theme.activo:
            raise AdminError(f'El tema "{theme.clave}" está desactivado. Actívalo primero.', "conflict")
        user.theme_id = theme.id
    db.commit()
    db.refresh(user)
    return user
