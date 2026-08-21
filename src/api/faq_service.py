"""
FAQ 服务 — DB 持久化 + 双轨刷新（关键词工具 + 向量库）

FAQ 数据流:
- DB 是唯一数据源（faqs 表）
- 聊天工具 KnowledgeSearchTool 用内存关键词检索（_faq_data）
- 向量库 VectorStore 存 FAQ 块（kind=faq，只重建 FAQ 部分，不动上传文档）
"""
import logging

logger = logging.getLogger(__name__)


async def reload_faqs() -> None:
    """从 DB 重新加载全部 FAQ：首次种子 → 刷新关键词工具 → 重建向量库 FAQ 部分"""
    from src.memory.database import get_session_factory
    from src.memory.faq_store import FaqStore
    from src.models.knowledge import FAQEntry
    from src.knowledge.loader import load_default_faqs
    from src.api.deps import get_tool_registry

    defaults = load_default_faqs()

    # 1. 首次启动时用内置 FAQ 种子（幂等）
    async with get_session_factory()() as db:
        try:
            store = FaqStore(db)
            if await store.count() == 0:
                for entry in defaults:
                    await store.create(FAQEntry(**entry))
                await db.commit()
                logger.info(f"FAQ 种子完成: {len(defaults)} 条")
            entries = await store.list_all()
        except Exception as e:
            await db.rollback()
            logger.error(f"FAQ 加载失败: {e}")
            return

    faq_dicts = [e.model_dump() for e in entries]

    # 2. 刷新聊天工具的关键词数据
    try:
        kb = get_tool_registry().get_tool("knowledge_search")
        if kb:
            kb.load_data(faq_dicts)
    except Exception as e:
        logger.warning(f"FAQ 刷新关键词工具失败: {e}")

    # 3. 重建向量库中的 FAQ 部分（不碰上传文档）
    try:
        from src.api.deps import get_knowledge_store

        vs = get_knowledge_store()
        if vs and vs._collection:
            vs.index_faqs(faq_dicts)
    except Exception as e:
        logger.warning(f"FAQ 刷新向量库失败: {e}")
