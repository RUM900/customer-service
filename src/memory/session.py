"""
会话持久化 — 管理客服会话的 CRUD

会话 (Session) 代表一次完整的客服对话，
关联所有消息、工单和状态。
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Text, DateTime, JSON, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.memory.database import Base
from src.models.conversation import ConversationStatus, Tier, Session as SessionModel

logger = logging.getLogger(__name__)


# ============================================================
# ORM Model
# ============================================================

class SessionRow(Base):
    """会话表"""
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=ConversationStatus.ACTIVE.value)
    current_tier: Mapped[str] = mapped_column(String(32), default=Tier.TRIAGE.value)
    active_agent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    escalation_count: Mapped[int] = mapped_column(Integer, default=0)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(64))


# ============================================================
# CRUD
# ============================================================

class SessionStore:
    """会话存储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, session: SessionModel) -> SessionModel:
        """创建新会话"""
        row = SessionRow(
            session_id=session.session_id,
            customer_id=session.customer_id,
            status=session.status.value,
            current_tier=session.current_tier.value,
            active_agent=session.active_agent,
            escalation_count=session.escalation_count,
            turn_count=session.turn_count,
            metadata_json=str(session.metadata) if session.metadata else None,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )
        self.db.add(row)
        await self.db.flush()
        logger.debug(f"会话已创建: {session.session_id}")
        return session

    async def get(self, session_id: str) -> Optional[SessionModel]:
        """获取会话"""
        stmt = select(SessionRow).where(SessionRow.session_id == session_id)
        result = await self.db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return self._row_to_model(row)

    async def update(self, session: SessionModel) -> SessionModel:
        """更新会话"""
        row = await self.db.get(SessionRow, session.session_id)
        if row is None:
            raise ValueError(f"会话不存在: {session.session_id}")

        row.status = session.status.value
        row.current_tier = session.current_tier.value
        row.active_agent = session.active_agent
        row.escalation_count = session.escalation_count
        row.turn_count = session.turn_count
        row.updated_at = datetime.now().isoformat()
        row.metadata_json = str(session.metadata) if session.metadata else None

        await self.db.flush()
        return session

    async def delete(self, session_id: str) -> bool:
        """删除会话"""
        row = await self.db.get(SessionRow, session_id)
        if row is None:
            return False
        await self.db.delete(row)
        await self.db.flush()
        return True

    async def list_by_customer(self, customer_id: str, limit: int = 20) -> list[SessionModel]:
        """获取客户的所有会话"""
        stmt = (
            select(SessionRow)
            .where(SessionRow.customer_id == customer_id)
            .order_by(SessionRow.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return [self._row_to_model(row) for row in result.scalars().all()]

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------

    @staticmethod
    def _row_to_model(row: SessionRow) -> SessionModel:
        """ORM row → Pydantic model"""
        import json
        meta = {}
        if row.metadata_json:
            try:
                meta = json.loads(row.metadata_json)
            except json.JSONDecodeError:
                pass

        return SessionModel(
            session_id=row.session_id,
            customer_id=row.customer_id,
            status=ConversationStatus(row.status),
            current_tier=Tier(row.current_tier),
            active_agent=row.active_agent,
            escalation_count=row.escalation_count,
            turn_count=row.turn_count,
            created_at=row.created_at,
            updated_at=row.updated_at,
            metadata=meta,
        )
