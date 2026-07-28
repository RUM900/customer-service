"""
Alembic 环境配置 — 异步 SQLAlchemy

用法:
    alembic revision --autogenerate -m "描述"
    alembic upgrade head
    alembic downgrade -1
"""
import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Alembic Config 对象
config = context.config

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ============================================================
# 数据库 URL — 从项目配置读取
# ============================================================

import config as app_config

# 将 asyncpg URL 转为标准格式（Alembic 需要同步 URL 来生成 migration）
# 对于 upgrade/downgrade，我们用 async engine
_sync_url = app_config.DATABASE_URL.replace("+asyncpg", "")
config.set_main_option("sqlalchemy.url", _sync_url)

# ============================================================
# 元数据 — 导入所有 ORM 模型
# ============================================================

from src.memory.database import Base

# 导入所有 ORM 模型（确保 Base.metadata 包含所有表）
from src.memory.session import SessionRow       # noqa: F401
from src.memory.conversation import MessageRow   # noqa: F401
from src.memory.ticket_store import TicketRow    # noqa: F401

target_metadata = Base.metadata

# ============================================================
# 运行模式
# ============================================================

def run_migrations_offline() -> None:
    """
    离线模式 — 生成 SQL 脚本（不连接数据库）
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在同步连接上执行迁移"""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    异步模式 — 生产环境使用
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式入口"""
    asyncio.run(run_async_migrations())


# ============================================================
# 入口
# ============================================================

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
