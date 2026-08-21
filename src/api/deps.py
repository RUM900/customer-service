"""
FastAPI 依赖注入

集中管理所有可注入的依赖，包括：
- 数据库会话
- Graph 实例
- Tool Registry
- Knowledge Store
"""
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.database import get_db as _get_db

import logging

logger = logging.getLogger(__name__)

# ============================================================
# 数据库
# ============================================================

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """注入数据库会话"""
    async for session in _get_db():
        yield session


# ============================================================
# Graph（懒加载单例）
# ============================================================

_graph = None


async def get_graph():
    """
    注入客服系统 LangGraph 工作流（异步获取，首次调用时编译）

    Graph 编译一次后缓存，所有请求复用同一个实例。
    编译时根据 CHECKPOINTER_BACKEND 选择 MemorySaver 或 PostgresSaver。
    """
    global _graph
    if _graph is None:
        from src.graph.workflow import build_customer_service_graph
        _graph = await build_customer_service_graph()
    return _graph


# ============================================================
# Tool Registry（懒加载单例）
# ============================================================

_registry = None


def get_tool_registry():
    """注入工具注册中心"""
    global _registry
    if _registry is None:
        from src.tools.registry import ToolRegistry
        _registry = ToolRegistry()
        _init_tools(_registry)
    return _registry


def _init_tools(registry):
    """初始化并注册所有默认工具"""
    from src.tools.knowledge_search import KnowledgeSearchTool
    from src.tools.crm import CRMLookupTool
    from src.tools.order import OrderLookupTool
    from src.tools.ticket import TicketCreateTool, TicketQueryTool
    from src.knowledge.loader import load_default_faqs

    # 知识库工具 — 加载 FAQ 数据到内存（关键词检索，13 条 FAQ 足够用）
    kb_tool = KnowledgeSearchTool()
    kb_tool.load_data(load_default_faqs())
    registry.register(kb_tool)
    registry.register(CRMLookupTool())
    registry.register(OrderLookupTool())
    registry.register(TicketCreateTool())
    registry.register(TicketQueryTool())

    # 工具绑定到 Agent
    registry.bind_to_agent("faq_answer", ["knowledge_search"])
    registry.bind_to_agent("technical", ["knowledge_search", "order_lookup", "ticket_create", "ticket_query"])
    registry.bind_to_agent("billing", ["crm_lookup", "order_lookup", "ticket_create"])
    registry.bind_to_agent("product", ["knowledge_search", "order_lookup"])
    registry.bind_to_agent("complaint", [
        "crm_lookup", "order_lookup", "ticket_create", "ticket_query"
    ])
    registry.bind_to_agent("supervisor", ["all"])


# ============================================================
# Knowledge Store（懒加载单例）
# ============================================================

_knowledge_store = None


def get_knowledge_store():
    """注入知识库向量存储"""
    global _knowledge_store
    if _knowledge_store is None:
        from src.knowledge.vector_store import VectorStore
        from src.knowledge.loader import load_default_faqs

        _knowledge_store = VectorStore()

        # 显式初始化 ChromaDB（VectorStore 采用懒加载，需手动触发）
        if not _knowledge_store.ensure_ready():
            logger.warning("ChromaDB 初始化失败，向量检索不可用，将使用内存关键词检索")
            return _knowledge_store

        # 如果集合为空，加载默认 FAQ
        if _knowledge_store.count() == 0:
            try:
                faqs = load_default_faqs()
                _knowledge_store.index_faqs(faqs)
            except Exception as e:
                logger.warning(f"FAQ 向量索引失败: {e}")

    return _knowledge_store
