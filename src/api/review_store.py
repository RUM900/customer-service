"""
人工审核队列 — HITL 审核案例（DB 持久化，内存兜底）

覆盖两类审核:
- supervisor_decision: Supervisor 高风险决策（图用 interrupt 暂停，审核后恢复执行）
- human_handoff: 客户要求转人工（图不暂停，管理员从队列批准/驳回）

调用方全部 await（函数已改为 async）。
"""
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 内存兜底队列（DB 不可用时使用）
_reviews: dict[str, dict] = {}


async def add_review(thread_id: str, review_context: dict) -> None:
    """添加一个待审核案例（DB 优先，内存兜底）"""
    review = {
        "thread_id": thread_id,
        "session_id": review_context.get("session_id", ""),
        "review_type": review_context.get("review_type", "supervisor_decision"),
        "decision": review_context.get("decision", {}),
        "review_items": review_context.get("review_items", []),
        "message": review_context.get("message", ""),
    }
    try:
        from src.memory.database import get_session_factory
        from src.memory.review import ReviewStore

        factory = get_session_factory()
        async with factory() as db:
            store = ReviewStore(db)
            if await store.has_pending(thread_id):
                logger.info(f"HITL: thread={thread_id} 已有待审核记录，跳过重复插入")
                return
            await store.create(review)
            await db.commit()
        logger.warning(f"HITL: 新审核案例 thread={thread_id}, type={review['review_type']}")
        return
    except Exception as e:
        logger.warning(f"HITL: DB 写入失败，使用内存队列: {e}")

    now = datetime.now().isoformat()
    _reviews[thread_id] = {
        "review_id": f"rev_mem_{len(_reviews)}",
        "thread_id": thread_id,
        "session_id": review.get("session_id", ""),
        "review_type": review.get("review_type", "supervisor_decision"),
        "decision": review.get("decision", {}),
        "review_items": review.get("review_items", []),
        "status": "pending",
        "message": review.get("message", ""),
        "reviewer_note": None,
        "created_at": now,
        "reviewed_at": None,
    }


async def get_pending_reviews() -> list[dict]:
    """获取所有待审核案例"""
    try:
        from src.memory.database import get_session_factory
        from src.memory.review import ReviewStore

        factory = get_session_factory()
        async with factory() as db:
            return await ReviewStore(db).get_pending()
    except Exception as e:
        logger.warning(f"HITL: DB 读取失败，使用内存队列: {e}")
        return [v for v in _reviews.values() if v["status"] == "pending"]


async def get_review(thread_id: str) -> Optional[dict]:
    """获取单个审核案例"""
    try:
        from src.memory.database import get_session_factory
        from src.memory.review import ReviewStore

        factory = get_session_factory()
        async with factory() as db:
            row = await ReviewStore(db).get_by_thread(thread_id)
            if row is not None:
                return row
    except Exception as e:
        logger.warning(f"HITL: DB 读取失败，使用内存队列: {e}")
    return _reviews.get(thread_id)


async def _resolve(thread_id: str, status: str, note: str) -> Optional[dict]:
    """批准/驳回的公共逻辑"""
    try:
        from src.memory.database import get_session_factory
        from src.memory.review import ReviewStore

        factory = get_session_factory()
        async with factory() as db:
            store = ReviewStore(db)
            row = await store.resolve(thread_id, status, note)
            if row is not None:
                await db.commit()
                return row
    except Exception as e:
        logger.warning(f"HITL: DB 写入失败，使用内存队列: {e}")

    # 内存兜底
    review = _reviews.get(thread_id)
    if not review or review["status"] != "pending":
        return None
    review["status"] = status
    review["reviewed_at"] = datetime.now().isoformat()
    review["reviewer_note"] = note
    return review


async def approve_review(thread_id: str, reviewer_note: str = "") -> Optional[dict]:
    """批准审核"""
    result = await _resolve(thread_id, "approved", reviewer_note)
    if result:
        logger.info(f"HITL: 审核通过 thread={thread_id}")
    return result


async def reject_review(thread_id: str, reason: str = "") -> Optional[dict]:
    """驳回审核"""
    result = await _resolve(thread_id, "rejected", reason)
    if result:
        logger.info(f"HITL: 审核驳回 thread={thread_id}, 原因: {reason}")
    return result
