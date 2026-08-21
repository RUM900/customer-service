"""
Agent 模型配置持久化 — 每个 Agent 使用的 LLM 模型
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.memory.database import Base

logger = logging.getLogger(__name__)

# 支持的 Agent 槽位（与 workflow 节点一一对应）
AGENT_SLOTS = ["triage", "technical", "billing", "product", "complaint", "supervisor"]


class AgentModelRow(Base):
    """Agent 模型配置表"""
    __tablename__ = "agent_models"

    agent_name: Mapped[str] = mapped_column(String(32), primary_key=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64))


class ModelConfigStore:
    """Agent 模型配置存储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all(self) -> dict[str, str]:
        stmt = select(AgentModelRow)
        result = await self.db.execute(stmt)
        return {row.agent_name: row.model for row in result.scalars().all()}

    async def get_by_agent(self, agent_name: str) -> Optional[str]:
        row = await self.db.get(AgentModelRow, agent_name)
        return row.model if row else None

    async def upsert(self, agent_name: str, model: str) -> None:
        row = await self.db.get(AgentModelRow, agent_name)
        if row is None:
            row = AgentModelRow(
                agent_name=agent_name,
                model=model,
                updated_at=datetime.now().isoformat(),
            )
            self.db.add(row)
        else:
            row.model = model
            row.updated_at = datetime.now().isoformat()
        await self.db.flush()
