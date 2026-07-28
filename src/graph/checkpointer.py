"""
LangGraph Checkpointer 管理

根据配置选择 MemorySaver（开发）或 PostgresSaver（生产）。

Usage:
    from src.graph.checkpointer import get_checkpointer
    checkpointer = await get_checkpointer()
"""
import logging
from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

import config

logger = logging.getLogger(__name__)

_checkpointer: Optional[BaseCheckpointSaver] = None


async def get_checkpointer() -> BaseCheckpointSaver:
    """
    获取 Checkpointer 实例（懒加载单例）

    - 开发环境: MemorySaver（内存，重启丢失）
    - 生产环境: PostgresSaver（持久化到 PostgreSQL）

    Returns:
        BaseCheckpointSaver 实例
    """
    global _checkpointer

    if _checkpointer is not None:
        return _checkpointer

    backend = config.CHECKPOINTER_BACKEND.lower()

    if backend == "memory":
        logger.info("使用 MemorySaver（内存模式，重启后状态丢失）")
        _checkpointer = MemorySaver()

    elif backend == "postgres":
        try:
            from langgraph.checkpoint.postgres import AsyncPostgresSaver

            db_url = config.CHECKPOINTER_DATABASE_URL or config.DATABASE_URL
            # AsyncPostgresSaver 需要同步 psycopg 连接字符串
            # 将 asyncpg 连接字符串转为 psycopg 格式
            postgres_url = _convert_to_sync_url(db_url)

            _checkpointer = AsyncPostgresSaver.from_conn_string(postgres_url)
            await _checkpointer.setup()
            logger.info(f"使用 PostgresSaver（PostgreSQL 持久化）: {postgres_url.split('@')[-1]}")

        except ImportError:
            logger.warning(
                "langgraph-checkpoint-postgres 未安装，回退到 MemorySaver。"
                "请执行: pip install langgraph-checkpoint-postgres"
            )
            _checkpointer = MemorySaver()

        except Exception as e:
            logger.error(f"PostgresSaver 初始化失败，回退到 MemorySaver: {e}")
            _checkpointer = MemorySaver()

    else:
        logger.warning(f"未知的 CHECKPOINTER_BACKEND: '{backend}'，使用 MemorySaver")
        _checkpointer = MemorySaver()

    return _checkpointer


async def reset_checkpointer():
    """重置 checkpointer 单例（测试用）"""
    global _checkpointer
    _checkpointer = None


def _convert_to_sync_url(async_url: str) -> str:
    """
    将 asyncpg 连接字符串转为标准 psycopg 格式

    postgresql+asyncpg://user:pass@host:5432/db
    → postgresql://user:pass@host:5432/db
    """
    return async_url.replace("+asyncpg", "")
