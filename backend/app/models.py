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


class Theme(Base):
    """Catálogo de temas visuales. El aspecto de cada tema vive en
    frontend/public/temas.css, en el bloque body[data-tema="<clave>"]."""
    __tablename__ = "themes"

    id: Mapped[int] = mapped_column(primary_key=True)
    clave: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # la que usa el CSS
    nombre: Mapped[str] = mapped_column(String(60))
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # en minúsculas
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # tema visual; NULL = diseño por defecto. Si se borra el tema, vuelve a NULL
    theme_id: Mapped[int | None] = mapped_column(
        ForeignKey("themes.id", ondelete="SET NULL", name="fk_users_theme_id"), nullable=True, index=True,
    )

    theme: Mapped[Theme | None] = relationship(lazy="joined")

    @property
    def theme_clave(self) -> str | None:
        """Clave del tema que debe ver el usuario, o None si no tiene o está desactivado."""
        return self.theme.clave if self.theme and self.theme.activo else None


class UserSession(Base):
    """Sesión iniciada en un dispositivo. Solo se guarda el hash del token."""
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)

    user: Mapped[User] = relationship(lazy="joined")


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    # dueño; NULL solo para notas anteriores a los usuarios, aún sin reclamar
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="fk_notes_user_id"), nullable=True, index=True,
    )
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
