import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import date as Date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, exists, func, literal, or_, select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from . import admin_service, auth
from .auth import current_user, get_db
from .events import broker
from .migrate import upgrade_database
from .models import Note, Task, User, UserSession, engine
from .parser import parse_note

APP_TZ = os.environ.get("APP_TZ", "America/Lima")


def local_today() -> Date:
    """Fecha de hoy en la zona horaria de la app (para !hoy, !mañana...)."""
    return datetime.now(ZoneInfo(APP_TZ)).date()

# La búsqueda ignora tildes si PostgreSQL tiene la extensión unaccent
# (viene incluida en la imagen oficial; se activa al arrancar).
SEARCH_UNACCENT = False


def enable_unaccent() -> bool:
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))
        return True
    except SQLAlchemyError:
        print("Aviso: no se pudo activar unaccent; la búsqueda distinguirá tildes.")
        return False


@asynccontextmanager
async def lifespan(_: FastAPI):
    # La base puede tardar unos segundos en aceptar conexiones.
    # Al conectar, se aplican las migraciones pendientes.
    for attempt in range(10):
        try:
            upgrade_database()
            break
        except OperationalError:
            if attempt == 9:
                raise
            time.sleep(2)
    global SEARCH_UNACCENT
    SEARCH_UNACCENT = enable_unaccent()
    broker.bind(asyncio.get_running_loop())
    yield


app = FastAPI(title="Notas API", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)


# Cada pestaña envía un id propio; así ignora los avisos de sus propios cambios
ClientId = Header(default=None, alias="X-Client-Id", max_length=64)


# ---------- Esquemas ----------

class Credentials(BaseModel):
    username: str = Field(max_length=60)
    password: str = Field(max_length=200)


class PasswordChange(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


class UserOut(BaseModel):
    username: str


class AuthConfig(BaseModel):
    allow_signup: bool
    has_users: bool


class NoteIn(BaseModel):
    raw: str = Field(min_length=1, max_length=20000)


class TaskPatch(BaseModel):
    done: bool


class TaskOut(BaseModel):
    id: int
    text: str
    done: bool
    assignee: str | None


class NoteOut(BaseModel):
    id: int
    title: str
    description: str
    raw: str
    priority: str | None
    tags: list[str]
    tasks: list[TaskOut]
    pending: int
    total: int
    due_date: Date | None
    created_at: datetime
    completed_at: datetime | None


class ParsedTaskOut(BaseModel):
    text: str
    assignee: str | None


class ParsedOut(BaseModel):
    title: str
    description: str
    tasks: list[ParsedTaskOut]
    tags: list[str]
    priority: str | None
    due_date: Date | None
    normalized_raw: str
    warnings: list[str]


class MetaOut(BaseModel):
    tags: list[str]
    priorities: list[str]
    assignees: list[str]
    days: list[str]      # días del mes con notas creadas (YYYY-MM-DD)
    due_days: list[str]  # días del mes en que vencen notas en cola


# ---------- Utilidades ----------

def to_out(n: Note) -> NoteOut:
    return NoteOut(
        id=n.id, title=n.title, description=n.description, raw=n.raw,
        priority=n.priority, tags=list(n.tags or []),
        tasks=[TaskOut(id=t.id, text=t.text, done=t.done, assignee=t.assignee) for t in n.tasks],
        pending=sum(not t.done for t in n.tasks), total=len(n.tasks),
        due_date=n.due_date, created_at=n.created_at, completed_at=n.completed_at,
    )


def get_note_or_404(db: Session, note_id: int, user: User) -> Note:
    note = db.get(Note, note_id)
    # una nota de otro usuario responde igual que una inexistente
    if not note or note.user_id != user.id:
        raise HTTPException(404, "La nota no existe o fue eliminada.")
    return note


def sync_completion(note: Note) -> None:
    """Una nota con pendientes sale de la cola cuando todos están marcados."""
    if not note.tasks:
        return  # las notas sin pendientes se completan a mano
    all_done = all(t.done for t in note.tasks)
    if all_done and note.completed_at is None:
        note.completed_at = datetime.now(timezone.utc)
    elif not all_done:
        note.completed_at = None


def apply_raw(note: Note, raw: str) -> None:
    parsed = parse_note(raw, local_today())
    note.raw = parsed.normalized_raw  # con las fechas relativas ya convertidas
    note.title = parsed.title
    note.description = parsed.description
    note.priority = parsed.priority
    note.tags = parsed.tags
    note.due_date = parsed.due_date

    # Al editar, conserva el estado de los pendientes que no cambiaron de texto
    # (cambiar solo el responsable no los desmarca)
    previous: dict[str, list[bool]] = {}
    for t in note.tasks:
        previous.setdefault(t.text.lower(), []).append(t.done)
    note.tasks.clear()
    for i, pt in enumerate(parsed.tasks):
        kept = previous.get(pt.text.lower())
        note.tasks.append(Task(
            text=pt.text, assignee=pt.assignee, done=kept.pop(0) if kept else False, position=i,
        ))
    sync_completion(note)


local_created = func.timezone(APP_TZ, Note.created_at)


def search_condition(q: str):
    """Coincidencia en título, descripción, pendientes o etiquetas,
    sin distinguir mayúsculas (ni tildes, si unaccent está activo)."""
    def norm(expr):
        expr = func.lower(expr)
        return func.unaccent(expr) if SEARCH_UNACCENT else expr

    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = norm(literal(f"%{escaped}%"))

    def matches(column):
        return norm(column).like(pattern, escape="\\")

    return or_(
        matches(Note.title),
        matches(Note.description),
        matches(func.array_to_string(Note.tags, " ")),
        exists().where(Task.note_id == Note.id, or_(matches(Task.text), matches(Task.assignee))),
    )


# ---------- Endpoints ----------

@app.get("/api/health")
def health():
    return {"ok": True}


# ---------- Cuentas ----------

# Cómo se traducen los errores de admin_service a respuestas HTTP
# (también servirá para los endpoints de una futura vista de administrador)
ADMIN_HTTP_STATUS = {"not_found": 404, "conflict": 409, "invalid": 422}

@app.get("/api/auth/config", response_model=AuthConfig)
def auth_config(db: Session = Depends(get_db)):
    has_users = db.scalar(select(func.count()).select_from(User)) > 0
    # la primera cuenta siempre se puede crear, aunque el registro esté cerrado
    return AuthConfig(allow_signup=auth.ALLOW_SIGNUP or not has_users, has_users=has_users)


@app.post("/api/auth/register", response_model=UserOut, status_code=201)
def register(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    has_users = db.scalar(select(func.count()).select_from(User)) > 0
    if has_users and not auth.ALLOW_SIGNUP:
        raise HTTPException(403, "El registro de cuentas nuevas está cerrado.")
    try:
        user = admin_service.create_user(db, body.username, body.password)
    except admin_service.AdminError as e:
        raise HTTPException(ADMIN_HTTP_STATUS[e.code], e.message) from None
    if not has_users:
        claimed = auth.claim_orphan_notes(db, user)
        print(f"Primera cuenta '{user.username}': recibió {claimed} notas existentes.")
    db.commit()
    auth.start_session(db, user, request, response)
    return UserOut(username=user.username)


@app.post("/api/auth/login", response_model=UserOut)
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    username = auth.normalize_username(body.username)
    auth.check_rate_limit(request, username)
    user = db.scalar(select(User).where(User.username == username))
    password_ok = auth.verify_password(body.password, user.password_hash if user else auth._DUMMY_HASH)
    if not user or not password_ok:
        auth.record_failure(request, username)
        raise HTTPException(401, "Usuario o contraseña incorrectos.")
    auth.clear_failures(request, username)
    auth.start_session(db, user, request, response)
    return UserOut(username=user.username)


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, db: Session = Depends(get_db)):
    response = Response(status_code=204)
    auth.end_session(db, request, response)
    return response


@app.get("/api/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return UserOut(username=user.username)


@app.post("/api/auth/password", status_code=204)
def change_password(
    body: PasswordChange, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db),
):
    auth.check_rate_limit(request, user.username)
    if not auth.verify_password(body.current, user.password_hash):
        auth.record_failure(request, user.username)
        raise HTTPException(403, "La contraseña actual no es correcta.")
    auth.validate_new_account(user.username, body.new)
    db_user = db.get(User, user.id)
    db_user.password_hash = auth.hash_password(body.new)
    # cierra las sesiones de los demás dispositivos; esta sigue abierta
    token_hash = auth._token_hash(request.cookies.get(auth.COOKIE_NAME, ""))
    db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.token_hash != token_hash))
    db.commit()
    return Response(status_code=204)


@app.post("/api/parse", response_model=ParsedOut)
def preview(body: NoteIn, user: User = Depends(current_user)):
    """Vista previa: interpreta el texto sin guardarlo."""
    p = parse_note(body.raw, local_today())
    return ParsedOut(
        title=p.title, description=p.description, tags=p.tags, priority=p.priority,
        tasks=[ParsedTaskOut(text=t.text, assignee=t.assignee) for t in p.tasks],
        due_date=p.due_date, normalized_raw=p.normalized_raw, warnings=p.warnings,
    )


@app.get("/api/notes", response_model=list[NoteOut])
def list_notes(
    status: Literal["active", "done", "all"] = "active",
    tag: str | None = None,
    priority: str | None = None,
    date: Date | None = None,
    q: str | None = Query(None, max_length=100),
    assignee: str | None = Query(None, max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    stmt = select(Note).where(Note.user_id == user.id)
    if status == "active":
        # Cola FIFO: la primera que entró es la primera que sale
        stmt = stmt.where(Note.completed_at.is_(None)).order_by(Note.created_at.asc(), Note.id.asc())
    elif status == "done":
        stmt = stmt.where(Note.completed_at.is_not(None)).order_by(Note.completed_at.desc())
    else:
        stmt = stmt.order_by(Note.created_at.asc(), Note.id.asc())

    if tag:
        stmt = stmt.where(Note.tags.contains([tag]))
    if priority:
        stmt = stmt.where(Note.priority.is_(None) if priority == "ninguna" else Note.priority == priority)
    if date:
        # el día seleccionado en el calendario: notas creadas o que vencen ese día
        stmt = stmt.where(or_(func.date(local_created) == date, Note.due_date == date))
    if assignee:
        stmt = stmt.where(exists().where(
            Task.note_id == Note.id, func.lower(Task.assignee) == assignee.lower(),
        ))
    if q and q.strip():
        stmt = stmt.where(search_condition(q.strip()))

    return [to_out(n) for n in db.scalars(stmt).all()]


@app.post("/api/notes", response_model=NoteOut, status_code=201)
def create_note(
    body: NoteIn, db: Session = Depends(get_db), client: str | None = ClientId, user: User = Depends(current_user),
):
    note = Note(raw=body.raw, title="", description="", tags=[], user_id=user.id)
    apply_raw(note, body.raw)
    db.add(note)
    db.commit()
    db.refresh(note)
    broker.publish(user.id, "created", note.id, client)
    return to_out(note)


@app.get("/api/notes/{note_id}", response_model=NoteOut)
def get_note(note_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return to_out(get_note_or_404(db, note_id, user))


@app.put("/api/notes/{note_id}", response_model=NoteOut)
def update_note(
    note_id: int, body: NoteIn, db: Session = Depends(get_db), client: str | None = ClientId,
    user: User = Depends(current_user),
):
    note = get_note_or_404(db, note_id, user)
    apply_raw(note, body.raw)
    db.commit()
    db.refresh(note)
    broker.publish(user.id, "updated", note.id, client)
    return to_out(note)


@app.delete("/api/notes/{note_id}", status_code=204)
def delete_note(
    note_id: int, db: Session = Depends(get_db), client: str | None = ClientId, user: User = Depends(current_user),
):
    db.delete(get_note_or_404(db, note_id, user))
    db.commit()
    broker.publish(user.id, "deleted", note_id, client)
    return Response(status_code=204)


@app.post("/api/notes/{note_id}/complete", response_model=NoteOut)
def complete_note(
    note_id: int, db: Session = Depends(get_db), client: str | None = ClientId, user: User = Depends(current_user),
):
    note = get_note_or_404(db, note_id, user)
    if note.tasks:
        raise HTTPException(409, "Esta nota sale de la cola cuando marcas todos sus pendientes.")
    note.completed_at = datetime.now(timezone.utc)
    db.commit()
    broker.publish(user.id, "updated", note.id, client)
    return to_out(note)


@app.post("/api/notes/{note_id}/reopen", response_model=NoteOut)
def reopen_note(
    note_id: int, db: Session = Depends(get_db), client: str | None = ClientId, user: User = Depends(current_user),
):
    note = get_note_or_404(db, note_id, user)
    if note.tasks:
        raise HTTPException(409, "Desmarca algún pendiente para devolverla a la cola.")
    note.completed_at = None
    db.commit()
    broker.publish(user.id, "updated", note.id, client)
    return to_out(note)


@app.patch("/api/tasks/{task_id}", response_model=NoteOut)
def toggle_task(
    task_id: int, body: TaskPatch, db: Session = Depends(get_db), client: str | None = ClientId,
    user: User = Depends(current_user),
):
    task = db.get(Task, task_id)
    if not task or task.note.user_id != user.id:
        raise HTTPException(404, "El pendiente no existe.")
    task.done = body.done
    note = task.note
    sync_completion(note)
    db.commit()
    db.refresh(note)
    broker.publish(user.id, "updated", note.id, client)
    return to_out(note)


@app.get("/api/events")
async def events(request: Request):
    """Conexión abierta por la que el servidor avisa de cada cambio."""
    with auth.SessionLocal() as db:
        user = auth.session_user(db, request)
        user_id = user.id if user else None
    if user_id is None:
        raise HTTPException(401, "Inicia sesión para continuar.")
    queue = broker.subscribe(user_id)

    async def stream():
        try:
            # retry: cada cuánto reintenta el navegador si se corta (ms)
            yield "retry: 3000\nevent: ready\ndata: {}\n\n"
            while not await request.is_disconnected():
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: change\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    # comentario vacío para que proxies y celulares no corten la conexión
                    yield ": ping\n\n"
        finally:
            broker.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/meta", response_model=MetaOut)
def meta(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"), db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    mine = Note.user_id == user.id
    tags = db.scalars(select(func.unnest(Note.tags)).where(mine).distinct()).all()
    priorities = db.scalars(select(Note.priority).where(mine, Note.priority.is_not(None)).distinct()).all()
    assignees = db.scalars(
        select(Task.assignee).join(Note, Task.note_id == Note.id).where(mine, Task.assignee.is_not(None)).distinct()
    ).all()
    days: list[str] = []
    due_days: list[str] = []
    if month:
        day_expr = func.to_char(local_created, "YYYY-MM-DD")
        days = db.scalars(
            select(day_expr).where(mine, func.to_char(local_created, "YYYY-MM") == month).distinct()
        ).all()
        due_expr = func.to_char(Note.due_date, "YYYY-MM-DD")
        due_days = db.scalars(
            select(due_expr).where(
                mine, Note.completed_at.is_(None), func.to_char(Note.due_date, "YYYY-MM") == month,
            ).distinct()
        ).all()
    return MetaOut(
        tags=sorted(tags, key=str.lower), priorities=sorted(priorities),
        assignees=sorted(assignees, key=str.lower), days=sorted(days), due_days=sorted(due_days),
    )
