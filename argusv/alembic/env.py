"""alembic/env.py — Alembic migration environment

Features:
  - POSTGRES_URL env var overrides alembic.ini sqlalchemy.url
  - compare_type=True so autogenerate detects column type changes
  - pgvector Vector type registered for autogenerate rendering
"""

import os
from logging.config import fileConfig

import sqlalchemy as sa
from sqlalchemy import engine_from_config, pool
from alembic import context
from alembic.autogenerate import renderers
from pathlib import Path
from dotenv import load_dotenv

# ── Import models so Alembic sees all table metadata ──────────────────────────
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db.models import Base  # noqa: E402
from pgvector.sqlalchemy import Vector  # noqa: E402

# ── Alembic Config object ─────────────────────────────────────────────────────
config = context.config

# Load local .env so Alembic uses the same DB URL as the app.
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=False)

# Load local .env so Alembic uses the same DB URL as the app.
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=False)

# Override URL from environment (docker / CI / prod) — keeps secrets out of INI.
postgres_url = os.getenv("POSTGRES_URL")
if postgres_url:
    config.set_main_option("sqlalchemy.url", postgres_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ── pgvector autogenerate support ─────────────────────────────────────────────
# Without this, `alembic revision --autogenerate` cannot render Vector columns.

@renderers.dispatch_for(Vector)
def render_vector_type(autogen_context, type_):
    autogen_context.imports.add("from pgvector.sqlalchemy import Vector")
    return f"Vector({type_.dim})"


# ── Migration runners ─────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
