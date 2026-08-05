import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.db.models.metadata import metadata


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db_session:
        yield db_session
    await engine.dispose()
