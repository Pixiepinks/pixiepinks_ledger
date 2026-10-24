"""Versioned schema migration entry point used by startup and deployment commands."""
from pathlib import Path
from alembic import command
from alembic.config import Config


def upgrade_database() -> None:
    config = Config(str(Path(__file__).with_name("alembic.ini")))
    command.upgrade(config, "head")
