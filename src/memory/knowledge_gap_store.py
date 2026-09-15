"""
知识盲区持久化 — Knowledge Gap 表（运营闭环数据底座）

记录 FAQ 未命中 / 转人工等知识盲区查询，支撑《待补充 FAQ 建议清单》。
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.memory.database import Base

logger = logging.getLogger(__name__)


class KnowledgeGapRow(Base):
    """知识盲区记录表"""
    __tablename__ = "knowledge_gaps"

    gap_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    query: Mapped[str] = mapped_column(String(512), index=True)
    route: Mapped[str] = mapped_column(String(32), default="faq_answer")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), index=True)


class KnowledgeGapStore:
    """知识盲区存储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, gap: dict) -> dict:
        from uuid import uuid4

        row = KnowledgeGapRow(
            gap_id=f"gap_{uuid4().hex[:8]}",
            session_id=gap.get("session_id", ""),
            query=gap.get("query", ""),
            route=gap.get("route", "faq_answer"),
            reason=gap.get("reason", ""),
            created_at=gap.get("created_at") or datetime.now().isoformat(),
        )
        self.db.add(row)
        await self.db.flush()
        return gap

    async def list_all(self, limit: int = 5000) -> list[dict]:
        stmt = (
            select(KnowledgeGapRow)
            .order_by(desc(KnowledgeGapRow.created_at))
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return [self._row_to_dict(row) for row in result.scalars().all()]

    @staticmethod
    def _row_to_dict(row: KnowledgeGapRow) -> dict:
        return {
            "gap_id": row.gap_id,
            "session_id": row.session_id,
            "query": row.query,
            "route": row.route,
            "reason": row.reason,
            "created_at": row.created_at,
        }