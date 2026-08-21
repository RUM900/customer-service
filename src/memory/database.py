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
        url = config.DATABASE_URL
        kwargs = {
            "pool_size": config.DATABASE_POOL_SIZE,
            "max_overflow": config.DATABASE_POOL_OVERFLOW,
            "echo": False,
        }
        if url.startswith("sqlite"):
            # SQLite 并发写锁缓解: busy timeout
            kwargs["connect_args"] = {"timeout": 30}
        _engine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):
            _enable_sqlite_wal(_engine)
        logger.info(f"数据库引擎已创建: {url.split('@')[-1]}")
    return _engine


def _enable_sqlite_wal(engine):
    """SQLite 启用 WAL 模式，缓解读写并发锁"""
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()


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
    """
    初始化数据库

    开发环境: 自动创建所有表（create_all）
    生产环境: 仅检查连接，提示使用 Alembic migration
    """
    import os
    env = os.getenv("ENVIRONMENT", "development").lower()

    if env == "production":
        # 生产环境不自动建表，应使用 alembic upgrade head
        logger.info(
            "生产环境: 跳过自动建表。请执行 'alembic upgrade head' 来应用数据库迁移。"
        )
        # 验证连接
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        logger.info("数据库连接验证成功")
    else:
        # 惰性导入所有 ORM 模型，确保 Base.metadata 包含全部表
        # （database.py 与各 store 模块存在循环依赖，不能在模块顶部 import）
        from src.memory.session import SessionRow
        from src.memory.conversation import MessageRow
        from src.memory.ticket_store import TicketRow
        from src.memory.user import UserRow
        from src.memory.model_config import AgentModelRow
        from src.memory.faq_store import FaqRow
        from src.memory.review import ReviewRow

        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("数据库表已初始化（开发模式: create_all）")
