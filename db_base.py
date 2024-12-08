import logging
from asyncio import current_task, shield
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_scoped_session,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.declarative import declarative_base

from settings import settings

logger = logging.getLogger(__name__)

meta = MetaData(
    naming_convention={
        "ix": "%(table_name)s_%(column_0_name)s_idx",
        "fk": "%(table_name)s_%(column_0_name)s_%(referred_table_name)s_fk",
        "pk": "%(table_name)s_pkey",
        "uq": "%(table_name)s_%(column_0_name)s_key",
        "ck": "%(table_name)s_%(constraint_name)s_check",
    },
)

Base = declarative_base(metadata=meta)

engine_to_bind = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = async_scoped_session(
    session_factory=async_sessionmaker(
        bind=engine_to_bind,
        expire_on_commit=True,
    ),
    scopefunc=current_task,
)


@asynccontextmanager
async def async_session_scope() -> AsyncGenerator[Any, AsyncSession]:
    session = SessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await shield(session.close())
        await shield(SessionLocal.remove())


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_scope() as db:
        yield db


async def db_setup():
    engine = create_async_engine(settings.database_url)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_tables_dangerous():
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def shutdown_session():
    await engine_to_bind.dispose()
