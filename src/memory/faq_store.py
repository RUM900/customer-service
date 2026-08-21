"""
FAQ 持久化 — 知识库 FAQ 条目的 CRUD
"""
import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.memory.database import Base
from src.models.knowledge import FAQEntry

logger = logging.getLogger(__name__)


class FaqRow(Base):
    """FAQ 表"""
    __tablename__ = "faqs"

    faq_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="general", index=True)
    tags_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(64))


class FaqStore:
    """FAQ 存储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, entry: FAQEntry) -> FAQEntry:
        now = datetime.now().isoformat()
        row = FaqRow(
            faq_id=entry.faq_id,
            question=entry.question,
            answer=entry.answer,
            category=entry.category,
            tags_json=json.dumps(entry.tags, ensure_ascii=False) if entry.tags else None,
            priority=entry.priority,
            source=entry.source,
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        await self.db.flush()
        return entry

    async def update(self, entry: FAQEntry) -> bool:
        row = await self.db.get(FaqRow, entry.faq_id)
        if row is None:
            return False
        row.question = entry.question
        row.answer = entry.answer
        row.category = entry.category
        row.tags_json = json.dumps(entry.tags, ensure_ascii=False) if entry.tags else None
        row.priority = entry.priority
        row.source = entry.source
        row.updated_at = datetime.now().isoformat()
        await self.db.flush()
        return True

    async def delete(self, faq_id: str) -> bool:
        row = await self.db.get(FaqRow, faq_id)
        if row is None:
            return False
        await self.db.delete(row)
        await self.db.flush()
        return True

    async def get(self, faq_id: str) -> Optional[FAQEntry]:
        row = await self.db.get(FaqRow, faq_id)
        return self._row_to_model(row) if row else None

    async def list_all(self) -> list[FAQEntry]:
        stmt = select(FaqRow).order_by(FaqRow.priority.desc())
        result = await self.db.execute(stmt)
        return [self._row_to_model(row) for row in result.scalars().all()]

    async def count(self) -> int:
        stmt = select(func.count()).select_from(FaqRow)
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    @staticmethod
    def _row_to_model(row: FaqRow) -> FAQEntry:
        tags = []
        if row.tags_json:
            try:
                tags = json.loads(row.tags_json)
            except json.JSONDecodeError:
                pass
        return FAQEntry(
            faq_id=row.faq_id,
            question=row.question,
            answer=row.answer,
            category=row.category,
            tags=tags,
            priority=row.priority,
            source=row.source,
        )
