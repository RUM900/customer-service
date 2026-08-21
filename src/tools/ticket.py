"""
工单工具 — 工单的创建、查询
"""
import logging

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
        from src.memory.database import get_session_factory

        ticket = Ticket(
            customer_id=customer_id,
            session_id=session_id,
            subject=subject,
            description=description,
            priority=TicketPriority(priority),
            status=TicketStatus.OPEN,
        )

        try:
            factory = get_session_factory()
            async with factory() as db:
                created = await TicketStore(db).create(ticket)
                await db.commit()
            logger.info(f"工单已创建: {created.ticket_id}")
            return {"created": True, "ticket": created.model_dump()}
        except Exception as e:
            logger.error(f"工单创建失败: {e}")
            return {"created": False, "error": str(e)}


class TicketQueryTool(BaseTool):
    """查询工单工具"""

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

    async def execute(
        self,
        ticket_id: str = "",
        customer_id: str = "",
    ) -> dict:
        from src.memory.database import get_session_factory

        try:
            factory = get_session_factory()
            async with factory() as db:
                store = TicketStore(db)
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
        except Exception as e:
            logger.error(f"工单查询失败: {e}")
            return {"found": False, "error": str(e)}
