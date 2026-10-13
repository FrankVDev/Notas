"""Pone la base de datos al día con las migraciones de Alembic.

Se ejecuta solo al arrancar la API. También se puede correr a mano:
    docker compose exec api python -m app.migrate
"""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from .models import engine

HERE = Path(__file__).resolve().parent


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE / "migrations"))
    return cfg


def upgrade_database() -> None:
    cfg = alembic_config()
    tables = set(inspect(engine).get_table_names())
    if "notes" in tables and "alembic_version" not in tables:
        # Base creada por una versión anterior, sin Alembic: sus tablas ya
        # corresponden a la migración 0001, así que solo se registra como tal.
        print("Base existente sin migraciones: se registra como versión 0001.")
        command.stamp(cfg, "0001")
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    upgrade_database()
