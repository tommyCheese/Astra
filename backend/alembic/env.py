from logging.config import fileConfig

from sqlalchemy import inspect, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.common.core.config import get_settings
from app.infrastructure.db.models.metadata import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata
CURRENT_BASELINE_REVISION = "0001_current_baseline"
SUPPORTED_REVISIONS = {
    CURRENT_BASELINE_REVISION,
    "0002_governed_subagent_runtime",
    "0003_concurrent_subagent_supervision",
    "0004_detach_scheduled_jobs",
    "0005_agent_context_compaction",
    "0006_runtime_profiles",
}


def get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    if "alembic_version" in inspect(connection).get_table_names():
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        ).scalar_one_or_none()
        if revision not in {None, *SUPPORTED_REVISIONS}:
            raise RuntimeError(
                "This database uses an obsolete Astra revision "
                f"({revision}); reset the database before starting Astra."
            )
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.begin() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    import asyncio

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
