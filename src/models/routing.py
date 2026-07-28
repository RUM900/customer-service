"""
路由模型 — 意图识别、情感分析、路由决策

Triage Agent 的核心输出模型。
"""
from enum import Enum
from uuid import uuid4
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# 意图分类
# ============================================================

class IntentType(str, Enum):
    """客户意图类型"""
    FAQ = "faq"                          # 常见问题
    TECHNICAL_SUPPORT = "technical_support"  # 技术支持
    BILLING_ACCOUNT = "billing_account"     # 账单/账户
    PRODUCT_INQUIRY = "product_inquiry"     # 产品咨询
    COMPLAINT = "complaint"                 # 投诉
    ORDER_STATUS = "order_status"           # 订单查询
    REFUND_REQUEST = "refund_request"       # 退款请求
    ACCOUNT_ISSUE = "account_issue"         # 账户问题
    OTHER = "other"                         # 其他


class Sentiment(str, Enum):
    """情感标签"""
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    ANGRY = "angry"


class Urgency(str, Enum):
    """紧急程度"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ============================================================
# Triage 结果
# ============================================================

class TriageResult(BaseModel):
    """
    Triage Agent 的完整输出

    包含意图分类、情感分析、紧急度判断、路由建议。
    """
    triage_id: str = Field(default_factory=lambda: f"triage_{uuid4().hex[:8]}")

    # 意图
    primary_intent: IntentType = Field(
        description="主要客户意图"
    )
    secondary_intents: list[IntentType] = Field(
        default_factory=list,
        description="次要意图（如果有）"
    )
    intent_confidence: float = Field(
        ge=0.0, le=1.0,
        description="意图识别置信度"
    )

    # 情感
    sentiment: Sentiment = Field(
        default=Sentiment.NEUTRAL,
        description="客户情感"
    )
    sentiment_detail: str = Field(
        default="",
        description="情感分析说明"
    )

    # 紧急度
    urgency: Urgency = Field(
        default=Urgency.LOW,
        description="紧急程度"
    )
    urgency_reason: str = Field(
        default="",
        description="紧急度判断依据"
    )

    # 路由建议
    recommended_agent: str = Field(
        default="technical",
        description="推荐路由到哪个 Agent: faq_answer | technical | billing | product | complaint"
    )
    can_self_handle: bool = Field(
        default=False,
        description="Triage 是否可以自己处理（如 FAQ 直接回答）"
    )
    routing_reason: str = Field(
        default="",
        description="路由理由"
    )

    # 摘要
    summary: str = Field(
        default="",
        description="客户问题的简短摘要"
    )

    # 是否需要立即转人工
    requires_immediate_human: bool = Field(
        default=False,
        description="是否需要立即转人工（如辱骂/威胁等场景）"
    )


# ============================================================
# 路由决策
# ============================================================

class RoutingDecision(BaseModel):
    """
    路由决策 — 决定下一步走向

    用于 LangGraph 条件路由。
    """
    decision_id: str = Field(default_factory=lambda: f"route_{uuid4().hex[:8]}")

    target_node: str = Field(
        description="目标节点: faq_answer | technical | billing | product | complaint | supervisor | human_handoff | __end__"
    )

    reason: str = Field(
        default="",
        description="决策理由"
    )

    escalation_reason: Optional[str] = Field(
        default=None,
        description="升级原因（仅 target 为 supervisor 时）"
    )

    requires_clarification: bool = Field(
        default=False,
        description="是否需要向客户澄清问题"
    )
