"""
工单持久化 — 工单 CRUD

工单是客服系统的核心实体，记录每个需要跟踪的问题。
"""
import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.memory.database import Base
from src.models.customer import TicketStatus, TicketPriority, Ticket

logger = logging.getLogger(__name__)


# ============================================================
# ORM Model
# ============================================================

class TicketRow(Base):
    """工单表"""
    __tablename__ = "tickets"

    ticket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(32), default=TicketPriority.MEDIUM.value)
    status: Mapped[str] = mapped_column(String(32), default=TicketStatus.OPEN.value)
    assigned_agent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(64))
    closed_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


# ============================================================
# CRUD
# ============================================================

class TicketStore:
    """工单存储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, ticket: Ticket) -> Ticket:
        """创建工单"""
        row = TicketRow(
            ticket_id=ticket.ticket_id,
            customer_id=ticket.customer_id,
            session_id=ticket.session_id,
            subject=ticket.subject,
            description=ticket.description,
            priority=ticket.priority.value,
            status=ticket.status.value,
            assigned_agent=ticket.assigned_agent,
            resolution=ticket.resolution,
            tags_json=json.dumps(ticket.tags, ensure_ascii=False) if ticket.tags else None,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            closed_at=ticket.closed_at,
        )
        self.db.add(row)
        await self.db.flush()
        logger.info(f"工单已创建: {ticket.ticket_id} — {ticket.subject}")
        return ticket

    async def get(self, ticket_id: str) -> Optional[Ticket]:
        """获取工单"""
        row = await self.db.get(TicketRow, ticket_id)
        if row is None:
            return None
        return self._row_to_model(row)

    async def update(self, ticket: Ticket) -> Ticket:
        """更新工单"""
        row = await self.db.get(TicketRow, ticket.ticket_id)
        if row is None:
            raise ValueError(f"工单不存在: {ticket.ticket_id}")

        row.subject = ticket.subject
        row.description = ticket.description
        row.priority = ticket.priority.value
        row.status = ticket.status.value
        row.assigned_agent = ticket.assigned_agent
        row.resolution = ticket.resolution
        row.tags_json = json.dumps(ticket.tags, ensure_ascii=False) if ticket.tags else None
        row.updated_at = datetime.now().isoformat()
        row.closed_at = ticket.closed_at

        await self.db.flush()
        return ticket

    async def list_by_customer(
        self, customer_id: str, status: Optional[str] = None, limit: int = 20
    ) -> list[Ticket]:
        """获取客户的工单列表"""
        from sqlalchemy import select
        stmt = select(TicketRow).where(TicketRow.customer_id == customer_id)
        if status:
            stmt = stmt.where(TicketRow.status == status)
        stmt = stmt.order_by(TicketRow.created_at.desc()).limit(limit)

        result = await self.db.execute(stmt)
        return [self._row_to_model(row) for row in result.scalars().all()]

    async def list_by_session(self, session_id: str) -> list[Ticket]:
        """获取会话关联的所有工单"""
        from sqlalchemy import select
        stmt = select(TicketRow).where(
            TicketRow.session_id == session_id
        ).order_by(TicketRow.created_at.desc())

        result = await self.db.execute(stmt)
        return [self._row_to_model(row) for row in result.scalars().all()]

    async def close(self, ticket_id: str, resolution: str = "") -> Optional[Ticket]:
        """关闭工单"""
        row = await self.db.get(TicketRow, ticket_id)
        if row is None:
            return None

        row.status = TicketStatus.CLOSED.value
        row.resolution = resolution
        row.closed_at = datetime.now().isoformat()
        row.updated_at = datetime.now().isoformat()

        await self.db.flush()
        return self._row_to_model(row)

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------

    @staticmethod
    def _row_to_model(row: TicketRow) -> Ticket:
        """ORM row → Pydantic model"""
        tags = []
        if row.tags_json:
            try:
                tags = json.loads(row.tags_json)
            except json.JSONDecodeError:
                pass

        return Ticket(
            ticket_id=row.ticket_id,
            customer_id=row.customer_id,
            session_id=row.session_id,
            subject=row.subject,
            description=row.description,
            priority=TicketPriority(row.priority),
            status=TicketStatus(row.status),
            assigned_agent=row.assigned_agent,
            resolution=row.resolution,
            tags=tags,
            created_at=row.created_at,
            updated_at=row.updated_at,
            closed_at=row.closed_at,
        )
