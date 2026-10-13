import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import date as Date, datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, literal, or_, select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from .events import broker
from .models import Base, Note, SessionLocal, Task, engine
from .parser import parse_note

APP_TZ = os.environ.get("APP_TZ", "America/Lima")

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
    # La base puede tardar unos segundos en aceptar conexiones
    for attempt in range(10):
        try:
            Base.metadata.create_all(engine)
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


def get_db():
    with SessionLocal() as db:
        yield db


# Cada pestaña envía un id propio; así ignora los avisos de sus propios cambios
ClientId = Header(default=None, alias="X-Client-Id", max_length=64)


# ---------- Esquemas ----------

class NoteIn(BaseModel):
    raw: str = Field(min_length=1, max_length=20000)


class TaskPatch(BaseModel):
    done: bool


class TaskOut(BaseModel):
    id: int
    text: str
    done: bool


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
    created_at: datetime
    completed_at: datetime | None


class ParsedOut(BaseModel):
    title: str
    description: str
    tasks: list[str]
    tags: list[str]
    priority: str | None
    warnings: list[str]


class MetaOut(BaseModel):
    tags: list[str]
    priorities: list[str]
    days: list[str]  # días del mes con notas creadas (YYYY-MM-DD)


# ---------- Utilidades ----------

def to_out(n: Note) -> NoteOut:
    return NoteOut(
        id=n.id, title=n.title, description=n.description, raw=n.raw,
        priority=n.priority, tags=list(n.tags or []),
        tasks=[TaskOut(id=t.id, text=t.text, done=t.done) for t in n.tasks],
        pending=sum(not t.done for t in n.tasks), total=len(n.tasks),
        created_at=n.created_at, completed_at=n.completed_at,
    )


def get_note_or_404(db: Session, note_id: int) -> Note:
    note = db.get(Note, note_id)
    if not note:
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
    parsed = parse_note(raw)
    note.raw = raw
    note.title = parsed.title
    note.description = parsed.description
    note.priority = parsed.priority
    note.tags = parsed.tags

    # Al editar, conserva el estado de los pendientes que no cambiaron de texto
    previous: dict[str, list[bool]] = {}
    for t in note.tasks:
        previous.setdefault(t.text.lower(), []).append(t.done)
    note.tasks.clear()
    for i, text in enumerate(parsed.tasks):
        kept = previous.get(text.lower())
        note.tasks.append(Task(text=text, done=kept.pop(0) if kept else False, position=i))
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
        exists().where(Task.note_id == Note.id, matches(Task.text)),
    )


# ---------- Endpoints ----------

@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/parse", response_model=ParsedOut)
def preview(body: NoteIn):
    """Vista previa: interpreta el texto sin guardarlo."""
    p = parse_note(body.raw)
    return ParsedOut(**p.__dict__)


@app.get("/api/notes", response_model=list[NoteOut])
def list_notes(
    status: Literal["active", "done", "all"] = "active",
    tag: str | None = None,
    priority: str | None = None,
    date: Date | None = None,
    q: str | None = Query(None, max_length=100),
    db: Session = Depends(get_db),
):
    stmt = select(Note)
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
        stmt = stmt.where(func.date(local_created) == date)
    if q and q.strip():
        stmt = stmt.where(search_condition(q.strip()))

    return [to_out(n) for n in db.scalars(stmt).all()]


@app.post("/api/notes", response_model=NoteOut, status_code=201)
def create_note(body: NoteIn, db: Session = Depends(get_db), client: str | None = ClientId):
    note = Note(raw=body.raw, title="", description="", tags=[])
    apply_raw(note, body.raw)
    db.add(note)
    db.commit()
    db.refresh(note)
    broker.publish("created", note.id, client)
    return to_out(note)


@app.get("/api/notes/{note_id}", response_model=NoteOut)
def get_note(note_id: int, db: Session = Depends(get_db)):
    return to_out(get_note_or_404(db, note_id))


@app.put("/api/notes/{note_id}", response_model=NoteOut)
def update_note(note_id: int, body: NoteIn, db: Session = Depends(get_db), client: str | None = ClientId):
    note = get_note_or_404(db, note_id)
    apply_raw(note, body.raw)
    db.commit()
    db.refresh(note)
    broker.publish("updated", note.id, client)
    return to_out(note)


@app.delete("/api/notes/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db), client: str | None = ClientId):
    db.delete(get_note_or_404(db, note_id))
    db.commit()
    broker.publish("deleted", note_id, client)
    return Response(status_code=204)


@app.post("/api/notes/{note_id}/complete", response_model=NoteOut)
def complete_note(note_id: int, db: Session = Depends(get_db), client: str | None = ClientId):
    note = get_note_or_404(db, note_id)
    if note.tasks:
        raise HTTPException(409, "Esta nota sale de la cola cuando marcas todos sus pendientes.")
    note.completed_at = datetime.now(timezone.utc)
    db.commit()
    broker.publish("updated", note.id, client)
    return to_out(note)


@app.post("/api/notes/{note_id}/reopen", response_model=NoteOut)
def reopen_note(note_id: int, db: Session = Depends(get_db), client: str | None = ClientId):
    note = get_note_or_404(db, note_id)
    if note.tasks:
        raise HTTPException(409, "Desmarca algún pendiente para devolverla a la cola.")
    note.completed_at = None
    db.commit()
    broker.publish("updated", note.id, client)
    return to_out(note)


@app.patch("/api/tasks/{task_id}", response_model=NoteOut)
def toggle_task(task_id: int, body: TaskPatch, db: Session = Depends(get_db), client: str | None = ClientId):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "El pendiente no existe.")
    task.done = body.done
    note = task.note
    sync_completion(note)
    db.commit()
    db.refresh(note)
    broker.publish("updated", note.id, client)
    return to_out(note)


@app.get("/api/events")
async def events(request: Request):
    """Conexión abierta por la que el servidor avisa de cada cambio."""
    queue = broker.subscribe()

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
def meta(month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"), db: Session = Depends(get_db)):
    tags = db.scalars(select(func.unnest(Note.tags)).distinct()).all()
    priorities = db.scalars(select(Note.priority).where(Note.priority.is_not(None)).distinct()).all()
    days: list[str] = []
    if month:
        day_expr = func.to_char(local_created, "YYYY-MM-DD")
        days = db.scalars(
            select(day_expr).where(func.to_char(local_created, "YYYY-MM") == month).distinct()
        ).all()
    return MetaOut(tags=sorted(tags, key=str.lower), priorities=sorted(priorities), days=sorted(days))
