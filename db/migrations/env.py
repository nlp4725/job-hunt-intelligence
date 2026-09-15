"""Alembic environment for the cloud Postgres database.

The URL comes from the config (tests set it) or JHI_DATABASE_URL. A SQLite URL
is refused: the local database is managed by db.session.init_db() and must
never be migrated from here.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from db.models import Base

config = context.config
if config.config_file_name and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

url = config.get_main_option("sqlalchemy.url") or os.environ.get("JHI_DATABASE_URL")
if not url:
    raise RuntimeError("Set JHI_DATABASE_URL to the Postgres database to migrate.")
if url.startswith("sqlite"):
    raise RuntimeError("Alembic manages only the cloud Postgres schema; the local SQLite DB uses db.session.init_db().")
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"options": "-c timezone=UTC"},
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
