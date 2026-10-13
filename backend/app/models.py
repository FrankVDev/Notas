import os
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, create_engine, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://notas:notas@localhost:5432/notas")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass

# Las tablas las crea y actualiza Alembic (ver app/migrations y app/migrate.py).
# Si cambias un modelo, crea una migración nueva; no basta con editar esta clase.


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[str] = mapped_column(Text)  # texto original, para poder editarlo
    priority: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # NULL = sigue en la cola; con fecha = ya salió
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # fecha límite (día completo, en la zona horaria de APP_TZ)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    tasks: Mapped[list["Task"]] = relationship(
        back_populates="note",
        cascade="all, delete-orphan",
        order_by="Task.position",
        lazy="selectin",
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    assignee: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)  # responsable

    note: Mapped[Note] = relationship(back_populates="tasks")
