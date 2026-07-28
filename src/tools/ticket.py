"""
工单工具 — 工单的创建、查询、更新

对接 PostgreSQL 中的工单存储。
"""
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.tools.base import BaseTool
from src.memory.ticket_store import TicketStore
from src.models.customer import Ticket, TicketPriority, TicketStatus

logger = logging.getLogger(__name__)


class TicketCreateTool(BaseTool):
    """创建工单工具"""

    name = "ticket_create"
    description = (
        "创建客服工单。当客户问题无法立即解决，需要后续跟踪时使用。"
        "参数包括主题、描述、优先级等。"
    )

    def __init__(self, db: AsyncSession):
        self.db = db

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "会话 ID"},
                "customer_id": {"type": "string", "description": "客户 ID"},
                "subject": {"type": "string", "description": "工单主题"},
                "description": {"type": "string", "description": "工单详细描述"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "优先级",
                },
            },
            "required": ["session_id", "subject", "description"],
        }

    async def execute(
        self,
        session_id: str,
        subject: str,
        description: str,
        customer_id: str = "",
        priority: str = "medium",
    ) -> dict:
        store = TicketStore(self.db)

        ticket = Ticket(
            customer_id=customer_id,
            session_id=session_id,
            subject=subject,
            description=description,
            priority=TicketPriority(priority),
            status=TicketStatus.OPEN,
        )

        created = await store.create(ticket)
        logger.info(f"工单已创建: {created.ticket_id}")
        return {
            "created": True,
            "ticket": created.model_dump(),
        }


class TicketQueryTool(BaseTool):
    """查询工单工具"""

    name = "ticket_query"
    description = "查询工单详情或客户的所有工单。"

    def __init__(self, db: AsyncSession):
        self.db = db

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "工单 ID"},
                "customer_id": {"type": "string", "description": "客户 ID（查询该客户所有工单）"},
            },
        }

    async def execute(
        self,
        ticket_id: str = "",
        customer_id: str = "",
    ) -> dict:
        store = TicketStore(self.db)

        if ticket_id:
            ticket = await store.get(ticket_id)
            if ticket is None:
                return {"found": False, "message": f"未找到工单 {ticket_id}"}
            return {"found": True, "ticket": ticket.model_dump()}

        if customer_id:
            tickets = await store.list_by_customer(customer_id)
            return {
                "found": len(tickets) > 0,
                "tickets": [t.model_dump() for t in tickets],
                "total": len(tickets),
            }

        return {"found": False, "message": "请提供 ticket_id 或 customer_id"}


# ============================================================
# 内存版工单工具（无需 DB，开发/测试用）
# ============================================================

_memory_tickets: dict[str, dict] = {}


class MockTicketCreateTool(BaseTool):
    """创建工单工具（内存版）"""

    name = "ticket_create"
    description = "创建客服工单。当问题无法立即解决需要跟踪时使用。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "会话 ID"},
                "customer_id": {"type": "string", "description": "客户 ID"},
                "subject": {"type": "string", "description": "工单主题"},
                "description": {"type": "string", "description": "工单描述"},
                "priority": {"type": "string", "description": "优先级: low|medium|high|critical"},
            },
            "required": ["session_id", "subject", "description"],
        }

    async def execute(
        self,
        session_id: str = "",
        subject: str = "",
        description: str = "",
        customer_id: str = "",
        priority: str = "medium",
    ) -> dict:
        ticket_id = f"ticket_{len(_memory_tickets) + 1:04d}"
        from datetime import datetime
        ticket = {
            "ticket_id": ticket_id,
            "session_id": session_id,
            "customer_id": customer_id,
            "subject": subject,
            "description": description,
            "priority": priority,
            "status": "open",
            "created_at": datetime.now().isoformat(),
        }
        _memory_tickets[ticket_id] = ticket
        logger.info(f"工单已创建(内存): {ticket_id} — {subject}")
        return {"created": True, "ticket": ticket}


class MockTicketQueryTool(BaseTool):
    """查询工单工具（内存版）"""

    name = "ticket_query"
    description = "查询工单详情或客户的所有工单。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "工单 ID"},
                "customer_id": {"type": "string", "description": "客户 ID（查询该客户所有工单）"},
            },
        }

    async def execute(self, ticket_id: str = "", customer_id: str = "") -> dict:
        if ticket_id:
            ticket = _memory_tickets.get(ticket_id)
            if ticket is None:
                return {"found": False, "message": f"未找到工单 {ticket_id}"}
            return {"found": True, "ticket": ticket}

        if customer_id:
            tickets = [t for t in _memory_tickets.values() if t.get("customer_id") == customer_id]
            return {"found": len(tickets) > 0, "tickets": tickets, "total": len(tickets)}

        return {"found": False, "message": "请提供 ticket_id 或 customer_id"}
