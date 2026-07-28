"""
对话历史持久化 — 消息的 CRUD

每条消息关联一个 session，支持按时间顺序查询完整对话历史。
"""
import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Text, DateTime, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.memory.database import Base
from src.models.conversation import MessageRole, Message

logger = logging.getLogger(__name__)


# ============================================================
# ORM Model
# ============================================================

class MessageRow(Base):
    """消息表"""
    __tablename__ = "messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    tool_calls_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # 消息序号
    timestamp: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(
        String(64), default=lambda: datetime.now().isoformat()
    )


# ============================================================
# CRUD
# ============================================================

class ConversationStore:
    """对话历史存储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def add_message(self, session_id: str, message: Message) -> Message:
        """添加一条消息"""
        # 获取下一个序号
        stmt = select(func.max(MessageRow.seq)).where(
            MessageRow.session_id == session_id
        )
        result = await self.db.execute(stmt)
        max_seq = result.scalar() or 0

        row = MessageRow(
            message_id=message.message_id,
            session_id=session_id,
            role=message.role.value,
            content=message.content,
            agent_name=message.agent_name,
            tool_calls_json=json.dumps(message.tool_calls, ensure_ascii=False) if message.tool_calls else None,
            metadata_json=json.dumps(message.metadata, ensure_ascii=False) if message.metadata else None,
            seq=max_seq + 1,
            timestamp=message.timestamp,
        )
        self.db.add(row)
        await self.db.flush()
        logger.debug(f"消息已保存: {message.message_id} (seq={max_seq + 1})")
        return message

    async def get_history(
        self,
        session_id: str,
        limit: int = 50,
        before_seq: Optional[int] = None,
    ) -> list[Message]:
        """
        获取对话历史

        Args:
            session_id: 会话 ID
            limit: 最大返回条数
            before_seq: 仅返回此序号之前的消息

        Returns:
            按 seq 升序排列的消息列表
        """
        stmt = (
            select(MessageRow)
            .where(MessageRow.session_id == session_id)
        )
        if before_seq is not None:
            stmt = stmt.where(MessageRow.seq < before_seq)

        stmt = stmt.order_by(MessageRow.seq.desc()).limit(limit)
        result = await self.db.execute(stmt)
        rows = result.scalars().all()

        # 反转回时间顺序
        return [self._row_to_model(row) for row in reversed(rows)]

    async def get_last_n_messages(
        self,
        session_id: str,
        n: int = 20,
    ) -> list[Message]:
        """获取最后 N 条消息"""
        return await self.get_history(session_id, limit=n)

    async def delete_session_messages(self, session_id: str) -> int:
        """删除会话的所有消息，返回删除数"""
        from sqlalchemy import delete
        stmt = delete(MessageRow).where(MessageRow.session_id == session_id)
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------

    @staticmethod
    def _row_to_model(row: MessageRow) -> Message:
        """ORM row → Pydantic model"""
        tool_calls = []
        if row.tool_calls_json:
            try:
                tool_calls = json.loads(row.tool_calls_json)
            except json.JSONDecodeError:
                pass

        metadata = {}
        if row.metadata_json:
            try:
                metadata = json.loads(row.metadata_json)
            except json.JSONDecodeError:
                pass

        return Message(
            message_id=row.message_id,
            role=MessageRole(row.role),
            content=row.content,
            agent_name=row.agent_name,
            tool_calls=tool_calls,
            metadata=metadata,
            timestamp=row.timestamp,
        )
