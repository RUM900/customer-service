"""
统一存储层 — DB 优先，内存自动降级

设计:
- 所有读写操作先尝试 PostgreSQL
- DB 不可用时自动降级到内存字典
- API 路由层无需关心底层实现
"""
import logging
from typing import Any, Optional
from datetime import datetime
from uuid import uuid4

from src.models.conversation import (
    Message, MessageRole, ConversationStatus, Tier,
    Session as SessionModel,
)
from src.models.customer import Ticket, TicketPriority, TicketStatus

import config

logger = logging.getLogger(__name__)


class StorageProvider:
    """
    统一存储抽象

    用法:
        store = StorageProvider()
        await store.connect()  # 尝试连 DB（可选）
        await store.save_message(sid, msg)
        history = await store.get_history(sid)
    """

    def __init__(self):
        self._db_available: Optional[bool] = None
        # 内存降级存储
        self._memory_sessions: dict[str, dict] = {}
        self._memory_messages: dict[str, list[dict]] = {}
        self._memory_tickets: dict[str, Ticket] = {}

    # ----------------------------------------------------------
    # 连接管理
    # ----------------------------------------------------------

    async def _ensure_db(self):
        """尝试获取 DB session，失败返回 None"""
        if self._db_available is False:
            return None
        try:
            from src.memory.database import get_session_factory
            from sqlalchemy import text
            factory = get_session_factory()
            session = factory()
            await session.execute(text("SELECT 1"))
            self._db_available = True
            return session
        except Exception:
            if 'session' in locals():
                await session.close()
            self._db_available = False
            return None

    async def _close_db(self, session) -> None:
        """安全关闭 DB session"""
        try:
            if session:
                await session.close()
        except Exception:
            pass

    async def _db_execute(self, operation) -> Any:
        """
        执行 DB 操作，自动管理 session 生命周期

        operation: async callable(db_session) -> result
        失败时返回 None，由调用方降级到内存
        """
        db = await self._ensure_db()
        if db is None:
            return None
        try:
            result = await operation(db)
            await db.commit()
            return result
        except Exception as e:
            logger.warning(f"DB 操作失败，降级内存: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            return None
        finally:
            await self._close_db(db)

    # ----------------------------------------------------------
    # 会话
    # ----------------------------------------------------------

    async def create_session(
        self, customer_id: Optional[str] = None
    ) -> SessionModel:
        """创建新会话"""
        sid = f"sess_{uuid4().hex[:12]}"
        now = datetime.now().isoformat()

        session = SessionModel(
            session_id=sid,
            customer_id=customer_id,
            status=ConversationStatus.ACTIVE,
            current_tier=Tier.TRIAGE,
            turn_count=0,
            created_at=now,
            updated_at=now,
        )

        async def _do(db):
            from src.memory.session import SessionStore
            await SessionStore(db).create(session)

        result = await self._db_execute(_do)
        if result is not None:
            logger.debug(f"DB: 会话已创建 {sid}")
            return session

        # 内存降级
        self._memory_sessions[sid] = session.model_dump()
        self._memory_messages[sid] = []
        logger.debug(f"内存: 会话已创建 {sid}")
        return session

    async def get_session(self, session_id: str) -> Optional[SessionModel]:
        """获取会话"""
        async def _do(db):
            from src.memory.session import SessionStore
            return await SessionStore(db).get(session_id)

        result = await self._db_execute(_do)
        if result is not None:
            return result

        # 内存降级
        data = self._memory_sessions.get(session_id)
        if data:
            return SessionModel(**data)
        return None

    async def update_session(self, session_id: str, **kwargs) -> None:
        """更新会话字段"""
        kwargs["updated_at"] = datetime.now().isoformat()

        async def _do(db):
            from src.memory.session import SessionStore
            store = SessionStore(db)
            existing = await store.get(session_id)
            if existing:
                for k, v in kwargs.items():
                    if hasattr(existing, k):
                        setattr(existing, k, v)
                await store.update(existing)

        result = await self._db_execute(_do)
        if result is not None:
            return

        # 内存降级
        if session_id in self._memory_sessions:
            self._memory_sessions[session_id].update(kwargs)

    # ----------------------------------------------------------
    # 消息
    # ----------------------------------------------------------

    async def save_message(self, session_id: str, message: Message) -> None:
        """保存一条消息"""
        async def _do(db):
            from src.memory.conversation import ConversationStore
            await ConversationStore(db).add_message(session_id, message)

        result = await self._db_execute(_do)
        if result is not None:
            return

        # 内存降级
        self._memory_messages.setdefault(session_id, []).append(message.model_dump())
        max_len = config.MAX_HISTORY_TURNS * 2
        if len(self._memory_messages[session_id]) > max_len:
            self._memory_messages[session_id] = self._memory_messages[session_id][-max_len:]

    async def get_history(
        self, session_id: str, limit: int = 50
    ) -> list[dict]:
        """获取对话历史"""
        async def _do(db):
            from src.memory.conversation import ConversationStore
            messages = await ConversationStore(db).get_last_n_messages(session_id, limit)
            return [m.model_dump() for m in messages]

        result = await self._db_execute(_do)
        if result is not None:
            return result

        # 内存降级
        msgs = self._memory_messages.get(session_id, [])
        return msgs[-limit:]

    # ----------------------------------------------------------
    # 工单
    # ----------------------------------------------------------

    async def create_ticket(
        self,
        session_id: str,
        subject: str,
        description: str,
        customer_id: str = "",
        priority: str = "medium",
    ) -> Ticket:
        """创建工单"""
        ticket = Ticket(
            session_id=session_id,
            customer_id=customer_id,
            subject=subject,
            description=description,
            priority=TicketPriority(priority),
            status=TicketStatus.OPEN,
        )

        async def _do(db):
            from src.memory.ticket_store import TicketStore
            await TicketStore(db).create(ticket)

        result = await self._db_execute(_do)
        if result is not None:
            logger.info(f"DB: 工单已创建 {ticket.ticket_id}")
            return ticket

        # 内存降级
        self._memory_tickets[ticket.ticket_id] = ticket
        return ticket

    async def get_ticket(self, ticket_id: str) -> Optional[Ticket]:
        """获取工单"""
        async def _do(db):
            from src.memory.ticket_store import TicketStore
            return await TicketStore(db).get(ticket_id)

        result = await self._db_execute(_do)
        if result is not None:
            return result

        # 内存降级
        return self._memory_tickets.get(ticket_id)

    # ----------------------------------------------------------
    # 生命周期
    # ----------------------------------------------------------

    async def close(self) -> None:
        """清理资源"""
        # 关闭 DB 连接（如有）
        try:
            from src.memory.database import close_db
            await close_db()
        except Exception:
            pass


# ============================================================
# 全局单例
# ============================================================

_storage: Optional[StorageProvider] = None


def get_storage() -> StorageProvider:
    """获取全局存储单例"""
    global _storage
    if _storage is None:
        _storage = StorageProvider()
    return _storage
