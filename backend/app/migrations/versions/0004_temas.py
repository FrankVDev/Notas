"""Catálogo de temas visuales y tema asignado a cada usuario

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Temas que ya tienen su bloque en frontend/public/temas.css.
# (tests/test_temas.py comprueba que cada clave tenga su CSS)
TEMAS_INICIALES = [
    {"clave": "nube", "nombre": "Nube", "descripcion": "Cielo pastel celeste y rosa, tickets con borde de nube"},
]


def upgrade() -> None:
    themes = op.create_table(
        "themes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clave", sa.String(30), nullable=False),
        sa.Column("nombre", sa.String(60), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("activo", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_themes_clave", "themes", ["clave"], unique=True)
    op.bulk_insert(themes, TEMAS_INICIALES)

    # columna opcional: los usuarios existentes siguen con el diseño por defecto
    op.add_column("users", sa.Column("theme_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_users_theme_id", "users", "themes", ["theme_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_users_theme_id", "users", ["theme_id"])


def downgrade() -> None:
    op.drop_index("ix_users_theme_id", table_name="users")
    op.drop_constraint("fk_users_theme_id", "users", type_="foreignkey")
    op.drop_column("users", "theme_id")
    op.drop_index("ix_themes_clave", table_name="themes")
    op.drop_table("themes")
