"""
人工审核持久化 — HITL 审核队列

覆盖两类审核:
- supervisor_decision: Supervisor 高风险决策（大额退款/账户删除等）
- human_handoff: 客户要求转人工的审核门槛
"""
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.memory.database import Base

logger = logging.getLogger(__name__)

REVIEW_TYPE_SUPERVISOR = "supervisor_decision"
REVIEW_TYPE_HANDOFF = "human_handoff"


class ReviewRow(Base):
    """审核队列表"""
    __tablename__ = "reviews"

    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    review_type: Mapped[str] = mapped_column(String(32), default=REVIEW_TYPE_SUPERVISOR)
    decision_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    review_items_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    reviewer_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64))
    reviewed_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ReviewStore:
    """审核队列存储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, review: dict) -> dict:
        row = ReviewRow(
            review_id=f"rev_{uuid4().hex[:8]}",
            thread_id=review["thread_id"],
            session_id=review.get("session_id", ""),
            review_type=review.get("review_type", REVIEW_TYPE_SUPERVISOR),
            decision_json=json.dumps(review.get("decision", {}), ensure_ascii=False, default=str)
            if review.get("decision")
            else None,
            review_items_json=json.dumps(review.get("review_items", []), ensure_ascii=False, default=str)
            if review.get("review_items")
            else None,
            status="pending",
            message=review.get("message", ""),
            created_at=datetime.now().isoformat(),
        )
        self.db.add(row)
        await self.db.flush()
        return self._row_to_dict(row)

    async def has_pending(self, thread_id: str) -> bool:
        stmt = (
            select(ReviewRow)
            .where(ReviewRow.thread_id == thread_id, ReviewRow.status == "pending")
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_pending(self) -> list[dict]:
        stmt = (
            select(ReviewRow)
            .where(ReviewRow.status == "pending")
            .order_by(ReviewRow.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return [self._row_to_dict(row) for row in result.scalars().all()]

    async def get_by_thread(self, thread_id: str) -> Optional[dict]:
        stmt = (
            select(ReviewRow)
            .where(ReviewRow.thread_id == thread_id)
            .order_by(ReviewRow.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        row = result.scalars().first()
        return self._row_to_dict(row) if row else None

    async def resolve(self, thread_id: str, status: str, note: str = "") -> Optional[dict]:
        """将 thread 的待审核记录置为 approved/rejected"""
        stmt = (
            select(ReviewRow)
            .where(ReviewRow.thread_id == thread_id, ReviewRow.status == "pending")
            .order_by(ReviewRow.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        row.status = status
        row.reviewer_note = note
        row.reviewed_at = datetime.now().isoformat()
        await self.db.flush()
        return self._row_to_dict(row)

    @staticmethod
    def _row_to_dict(row: ReviewRow) -> dict:
        decision = {}
        if row.decision_json:
            try:
                decision = json.loads(row.decision_json)
            except json.JSONDecodeError:
                pass
        items = []
        if row.review_items_json:
            try:
                items = json.loads(row.review_items_json)
            except json.JSONDecodeError:
                pass
        return {
            "review_id": row.review_id,
            "thread_id": row.thread_id,
            "session_id": row.session_id,
            "review_type": row.review_type,
            "decision": decision,
            "review_items": items,
            "status": row.status,
            "message": row.message,
            "reviewer_note": row.reviewer_note,
            "created_at": row.created_at,
            "reviewed_at": row.reviewed_at,
        }
