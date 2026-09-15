"""
知识盲区记录 — Knowledge Gap（知识库自闭环的数据底座）

用途:
- 记录 FAQ 未命中 / 低置信 / 转人工的高频客户问题
- 提供聚类统计，供运营生成《待补充 FAQ 建议清单》

数据流:
- faq_answer_node 检索未命中(score < 阈值) → 记录 gap
- human_handoff_node 转人工 → 记录 gap
- 运营后台 GET /admin/knowledge/gap-analysis 聚类分析 → 生成新 FAQ
"""
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 内存兜底队列（DB 不可用时使用）
_gaps: list[dict] = []


async def record_gap(
    session_id: str,
    query: str,
    route: str,
    reason: str = "",
) -> None:
    """记录一条知识盲区（DB 优先，内存兜底）"""
    if not query or not query.strip():
        return

    gap = {
        "session_id": session_id,
        "query": query.strip()[:500],
        "route": route,          # faq_answer | human_handoff | specialist
        "reason": reason[:300],
        "created_at": datetime.now().isoformat(),
    }

    try:
        from src.memory.database import get_session_factory
        from src.memory.knowledge_gap_store import KnowledgeGapStore

        factory = get_session_factory()
        async with factory() as db:
            await KnowledgeGapStore(db).create(gap)
            await db.commit()
        logger.info(f"KnowledgeGap: 记录盲区 query='{gap['query'][:40]}' route={route}")
        return
    except Exception as e:
        logger.warning(f"KnowledgeGap: DB 写入失败，使用内存队列: {e}")

    _gaps.append(gap)
    if len(_gaps) > 2000:
        _gaps.pop(0)  # 防止内存无限增长


async def get_gap_analysis(min_count: int = 2) -> list[dict]:
    """
    聚类分析知识盲区

    按规范化后的 query 聚合，统计出现次数，返回建议补充的 FAQ 清单。
    """
    rows = await _all_rows()

    clusters: dict[str, dict] = {}
    for r in rows:
        key = _normalize_query(r["query"])
        if key not in clusters:
            clusters[key] = {
                "query": r["query"],
                "count": 0,
                "routes": set(),
                "reasons": [],
                "first_seen": r["created_at"],
                "last_seen": r["created_at"],
                "session_ids": [],
            }
        c = clusters[key]
        c["count"] += 1
        c["routes"].add(r["route"])
        if r["reason"]:
            c["reasons"].append(r["reason"])
        if r["created_at"] > c["last_seen"]:
            c["last_seen"] = r["created_at"]
        if len(c["session_ids"]) < 10:
            c["session_ids"].append(r["session_id"])

    result = []
    for c in clusters.values():
        if c["count"] >= min_count:
            result.append({
                "query": c["query"],
                "count": c["count"],
                "routes": sorted(c["routes"]),
                "top_reasons": c["reasons"][:3],
                "first_seen": c["first_seen"],
                "last_seen": c["last_seen"],
                "session_ids": c["session_ids"],
                "suggested_faq": {
                    "question": c["query"][:100],
                    "answer": "（待业务人员补充标准答案）",
                    "category": "general",
                },
            })

    # 按出现次数降序
    result.sort(key=lambda x: x["count"], reverse=True)
    return result


async def _all_rows() -> list[dict]:
    """读取全部盲区记录（DB 优先，内存兜底）"""
    try:
        from src.memory.database import get_session_factory
        from src.memory.knowledge_gap_store import KnowledgeGapStore

        factory = get_session_factory()
        async with factory() as db:
            return await KnowledgeGapStore(db).list_all()
    except Exception as e:
        logger.warning(f"KnowledgeGap: DB 读取失败，使用内存队列: {e}")
        return list(_gaps)


def _normalize_query(query: str) -> str:
    """规范化查询（去空白/标点/语气词，用于聚类）"""
    import re

    text = query.lower().strip()
    text = re.sub(r"[\s，。！？、,.!?]+", "", text)
    # 去掉常见语气词
    for word in ("你好", "您好", "请问", "麻烦", "谢谢"):
        text = text.replace(word, "")
    return text or query.strip()
