"""alembic/env.py — Alembic migration environment"""

import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from pathlib import Path
from dotenv import load_dotenv

# Import all models so Alembic can autogenerate migrations
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from db.models import Base

config = context.config

# Load local .env so Alembic uses the same DB URL as the app.
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=False)

# Override sqlalchemy.url with env var if set
postgres_url = os.getenv("POSTGRES_URL")
if postgres_url:
    config.set_main_option("sqlalchemy.url", postgres_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
