"""Fecha límite en las notas y responsable en los pendientes

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # columnas opcionales: las notas existentes quedan sin fecha ni responsable
    op.add_column("notes", sa.Column("due_date", sa.Date(), nullable=True))
    op.create_index("ix_notes_due_date", "notes", ["due_date"])
    op.add_column("tasks", sa.Column("assignee", sa.String(100), nullable=True))
    op.create_index("ix_tasks_assignee", "tasks", ["assignee"])


def downgrade() -> None:
    op.drop_index("ix_tasks_assignee", table_name="tasks")
    op.drop_column("tasks", "assignee")
    op.drop_index("ix_notes_due_date", table_name="notes")
    op.drop_column("notes", "due_date")
