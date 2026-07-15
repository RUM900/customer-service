"""
数据库连接管理 — async SQLAlchemy + asyncpg

提供:
- 异步 engine 创建与关闭
- 异步 session 工厂
- FastAPI 依赖注入 compatible
"""
import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

import config

logger = logging.getLogger(__name__)

# ============================================================
# Engine & Session Factory
# ============================================================

_engine = None
_session_factory = None


def get_engine():
    """懒加载异步 engine"""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            config.DATABASE_URL,
            pool_size=config.DATABASE_POOL_SIZE,
            max_overflow=config.DATABASE_POOL_OVERFLOW,
            echo=False,
        )
        logger.info(f"数据库引擎已创建: {config.DATABASE_URL.split('@')[-1]}")
    return _engine


def get_session_factory() -> async_sessionmaker:
    """懒加载异步 session 工厂"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话（FastAPI 依赖注入用）

    Usage:
        @app.get("/items")
        async def read_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db():
    """关闭数据库连接（应用关闭时调用）"""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("数据库连接已关闭")


# ============================================================
# ORM Base
# ============================================================

class Base(DeclarativeBase):
    """SQLAlchemy ORM 基类"""
    pass


# ============================================================
# 数据库初始化（创建表）
# ============================================================

async def init_db():
    """初始化数据库 — 创建所有表"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表已初始化")
