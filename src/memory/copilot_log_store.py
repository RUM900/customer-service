"""
Copilot 调用审计持久化 — 采纳率/成本/质量度量数据底座

用途:
- 记录每次 Copilot 建议（内容快照 + 是否被采纳 + 编辑量）
- 支撑统计看板: 采纳率 / 按意图采纳率 / 平均编辑量 / 单次成本
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Integer, Float, select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.memory.database import Base

logger = logging.getLogger(__name__)


class CopilotLogRow(Base):
    """Copilot 调用审计表"""
    __tablename__ = "copilot_logs"

    log_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    agent_id: Mapped[str] = mapped_column(String(64), default="")
    customer_message: Mapped[str] = mapped_column(Text, default="")
    suggested_reply: Mapped[str] = mapped_column(Text, default="")
    context_summary: Mapped[str] = mapped_column(Text, default="")
    suggested_tools_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    knowledge_refs_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    adopted: Mapped[int] = mapped_column(Integer, default=0)     # 0 未采纳 / 1 原样采纳 / 2 修改后采纳
    edited_delta: Mapped[str] = mapped_column(Text, default="")  # 坐席修改内容（采纳时的原文快照）
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[str] = mapped_column(String(64), index=True)
    adopted_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class CopilotLogStore:
    """Copilot 审计存储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, log: dict) -> dict:
        from uuid import uuid4

        row = CopilotLogRow(
            log_id=f"cop_{uuid4().hex[:8]}",
            session_id=log.get("session_id", ""),
            agent_id=log.get("agent_id", ""),
            customer_message=log.get("customer_message", ""),
            suggested_reply=log.get("suggested_reply", ""),
            context_summary=log.get("context_summary", ""),
            suggested_tools_json=_dumps(log.get("suggested_tools", [])),
            knowledge_refs_json=_dumps(log.get("knowledge_refs", []))
            if log.get("knowledge_refs") else None,
            adopted=log.get("adopted", 0),
            edited_delta=log.get("edited_delta", ""),
            latency_ms=log.get("latency_ms", 0),
            tokens=log.get("tokens", 0),
            cost_cny=log.get("cost_cny", 0.0),
            created_at=datetime.now().isoformat(),
        )
        self.db.add(row)
        await self.db.flush()
        return {"log_id": row.log_id, **log}

    async def get(self, log_id: str) -> Optional[dict]:
        row = await self.db.get(CopilotLogRow, log_id)
        return self._row_to_dict(row) if row else None

    async def mark_adopted(self, log_id: str, adopted: int, edited_delta: str = "") -> Optional[dict]:
        """标记采纳状态: 1=原样采纳 2=修改后采纳"""
        row = await self.db.get(CopilotLogRow, log_id)
        if row is None:
            return None
        row.adopted = adopted
        row.edited_delta = edited_delta[:1000]
        row.adopted_at = datetime.now().isoformat()
        await self.db.flush()
        return self._row_to_dict(row)

    async def stats(self, days: int = 30) -> dict:
        """统计看板: 采纳率 / 编辑量 / 成本"""
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        stmt = (
            select(CopilotLogRow)
            .where(CopilotLogRow.created_at >= cutoff)
            .order_by(desc(CopilotLogRow.created_at))
        )
        result = await self.db.execute(stmt)
        rows = [self._row_to_dict(r) for r in result.scalars().all()]

        total = len(rows)
        adopted = sum(1 for r in rows if r["adopted"] in (1, 2))
        as_is = sum(1 for r in rows if r["adopted"] == 1)
        edited = sum(1 for r in rows if r["adopted"] == 2)
        edited_len = sum(len(r["edited_delta"]) for r in rows if r["edited_delta"])
        total_cost = sum(r["cost_cny"] for r in rows)
        avg_latency = sum(r["latency_ms"] for r in rows) / total if total else 0
        total_tokens = sum(r["tokens"] for r in rows)

        # 按会话意图粗聚类（简单按关键词归类）
        intent_buckets: dict[str, dict] = {}
        for r in rows:
            bucket = _classify_intent(r["customer_message"])
            b = intent_buckets.setdefault(bucket, {"total": 0, "adopted": 0})
            b["total"] += 1
            if r["adopted"] in (1, 2):
                b["adopted"] += 1

        return {
            "days": days,
            "total": total,
            "adopt_rate": round(adopted / total, 4) if total else None,
            "as_is_rate": round(as_is / total, 4) if total else None,
            "edited_rate": round(edited / total, 4) if total else None,
            "avg_edit_len": round(edited_len / edited, 1) if edited else 0,
            "avg_latency_ms": round(avg_latency, 1),
            "total_tokens": total_tokens,
            "total_cost_cny": round(total_cost, 6),
            "by_intent": [
                {"intent": k, "total": v["total"], "adopt_rate": round(v["adopted"] / v["total"], 4)}
                for k, v in sorted(intent_buckets.items(), key=lambda x: -x[1]["total"])
            ],
        }

    @staticmethod
    def _row_to_dict(row: CopilotLogRow) -> dict:
        import json
        tools = []
        if row.suggested_tools_json:
            try:
                tools = json.loads(row.suggested_tools_json)
            except json.JSONDecodeError:
                pass
        refs = []
        if row.knowledge_refs_json:
            try:
                refs = json.loads(row.knowledge_refs_json)
            except json.JSONDecodeError:
                pass
        return {
            "log_id": row.log_id,
            "session_id": row.session_id,
            "agent_id": row.agent_id,
            "customer_message": row.customer_message,
            "suggested_reply": row.suggested_reply,
            "context_summary": row.context_summary,
            "suggested_tools": tools,
            "knowledge_refs": refs,
            "adopted": row.adopted,
            "edited_delta": row.edited_delta,
            "latency_ms": row.latency_ms,
            "tokens": row.tokens,
            "cost_cny": row.cost_cny,
            "created_at": row.created_at,
            "adopted_at": row.adopted_at,
        }


def _dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)


def _classify_intent(text: str) -> str:
    """按关键词粗分类（用于按意图统计采纳率）"""
    text = text or ""
    rules = [
        ("退款", "refund"),
        ("投诉", "complaint"),
        ("订单", "order"),
        ("物流", "logistics"),
        ("技术|闪退|崩溃|连接", "technical"),
        ("发票|账单|扣款|余额", "billing"),
        ("产品|规格|参数", "product"),
        ("人工", "handoff"),
    ]
    for kw, label in rules:
        import re
        if re.search(kw, text):
            return label
    return "other"