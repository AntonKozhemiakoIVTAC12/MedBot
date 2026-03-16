from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base


def create_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(database_url, future=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_database(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        engine = session.bind
        if engine is None:
            raise RuntimeError("Async session is not bound to an engine.")

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
