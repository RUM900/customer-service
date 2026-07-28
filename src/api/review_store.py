"""
人工审核队列 — HITL 暂停的案例

当 Supervisor 设置 require_human_review=true 时，
案例被加入此队列，等待人工审核。
"""
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 内存审核队列（生产环境应持久化到 DB）
_reviews: dict[str, dict] = {}


def add_review(thread_id: str, review_context: dict) -> None:
    """添加一个待审核案例"""
    _reviews[thread_id] = {
        "thread_id": thread_id,
        "session_id": review_context.get("session_id", ""),
        "decision": review_context.get("decision", {}),
        "review_items": review_context.get("review_items", []),
        "status": "pending",          # pending | approved | rejected
        "created_at": datetime.now().isoformat(),
        "reviewed_at": None,
        "reviewer_note": None,
    }
    logger.warning(f"HITL: 新审核案例 thread={thread_id}, items={review_context.get('review_items', [])}")


def get_pending_reviews() -> list[dict]:
    """获取所有待审核案例"""
    return [v for v in _reviews.values() if v["status"] == "pending"]


def get_review(thread_id: str) -> Optional[dict]:
    """获取单个审核案例"""
    return _reviews.get(thread_id)


def approve_review(thread_id: str, reviewer_note: str = "") -> Optional[dict]:
    """批准审核"""
    review = _reviews.get(thread_id)
    if not review or review["status"] != "pending":
        return None
    review["status"] = "approved"
    review["reviewed_at"] = datetime.now().isoformat()
    review["reviewer_note"] = reviewer_note
    logger.info(f"HITL: 审核通过 thread={thread_id}")
    return review


def reject_review(thread_id: str, reason: str = "") -> Optional[dict]:
    """驳回审核"""
    review = _reviews.get(thread_id)
    if not review or review["status"] != "pending":
        return None
    review["status"] = "rejected"
    review["reviewed_at"] = datetime.now().isoformat()
    review["reviewer_note"] = reason
    logger.info(f"HITL: 审核驳回 thread={thread_id}, 原因: {reason}")
    return review
