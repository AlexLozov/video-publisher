"""Run Alembic migrations from code (the desktop app upgrades its database on start)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def upgrade_database(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade_database(url: str, revision: str) -> None:
    command.downgrade(alembic_config(url), revision)
