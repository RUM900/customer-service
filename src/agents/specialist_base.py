"""
Specialist Agent 共用模型和基类

所有 Tier 2 Agent 共享的输出格式和基础行为。
"""
from uuid import uuid4
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# Specialist 输出模型
# ============================================================

class SpecialistResponse(BaseModel):
    """
    Specialist Agent 的统一输出

    所有 Tier 2 Agent（Technical/Billing/Product/Complaint）都输出此格式。
    """
    response_id: str = Field(default_factory=lambda: f"resp_{uuid4().hex[:8]}")

    # 回复给客户的内容
    reply_to_customer: str = Field(
        description="给客户的回复内容（自然语言，友好专业）"
    )

    # 诊断/分析
    diagnosis: str = Field(
        default="",
        description="问题诊断或分析结论"
    )

    # 解决方案
    solution: str = Field(
        default="",
        description="解决方案详情"
    )
    action_items: list[str] = Field(
        default_factory=list,
        description="具体操作步骤"
    )

    # 状态标记
    is_resolved: bool = Field(
        default=False,
        description="问题是否已解决"
    )
    needs_escalation: bool = Field(
        default=False,
        description="是否需要升级到 Supervisor"
    )
    escalation_reason: str = Field(
        default="",
        description="升级原因（仅在 needs_escalation=true 时填写）"
    )

    # 工具调用
    tools_to_use: list[str] = Field(
        default_factory=list,
        description="需要调用的工具名称列表"
    )

    tool_calls: list[dict] = Field(
        default_factory=list,
        description=(
            "工具调用的参数（优先于 tools_to_use 单独提供参数）。"
            "格式: [{'tool': 'order_lookup', 'args': {'order_id': 'ord_001'}}]。"
            "参数从对话历史/客户消息中提取：订单号用 order_id、客户用 customer_id、"
            "知识库搜索用 query；无法确定参数时可省略 args，系统会自动提取。"
        ),
    )

    # 置信度
    confidence: float = Field(
        default=0.8,
        ge=0.0, le=1.0,
        description="回复置信度"
    )

    # 后续建议
    follow_up_questions: list[str] = Field(
        default_factory=list,
        description="需要向客户确认的后续问题"
    )

    # 是否发工单
    should_create_ticket: bool = Field(
        default=False,
        description="是否需要创建工单"
    )


class SupervisorDecision(BaseModel):
    """
    Supervisor Agent 的输出

    监管 Agent 的决策结果。
    """
    decision_id: str = Field(default_factory=lambda: f"sup_{uuid4().hex[:8]}")

    # 决策
    action: str = Field(
        description="决策动作: resolve | escalate_to_human | coordinate | reject"
    )
    reasoning: str = Field(
        description="决策理由"
    )

    # 回复
    reply_to_customer: str = Field(
        default="",
        description="给客户的最终回复"
    )

    # 解决方案
    final_solution: str = Field(
        default="",
        description="最终解决方案"
    )
    compensation: str = Field(
        default="",
        description="补偿方案（退款/优惠券/积分等）"
    )

    # HITL: 需要人工审核的高风险决策
    require_human_review: bool = Field(
        default=False,
        description="是否需要人工审核（大额退款/账户删除/法律风险等）"
    )
    review_items: list[str] = Field(
        default_factory=list,
        description="需要审核的具体项目，如['退款500元', '补偿300元优惠券']"
    )

    # 是否需要人工
    handoff_required: bool = Field(
        default=False,
        description="是否需要转人工"
    )
    handoff_summary: str = Field(
        default="",
        description="给人工客服的摘要"
    )

    # 跨域协调
    coordinate_agents: list[str] = Field(
        default_factory=list,
        description="需要协调的 Agent 列表"
    )

    # 是否创工单
    should_create_ticket: bool = Field(
        default=True,
        description="升级案例默认创建工单"
    )


# ============================================================
# 共享上下文构建
# ============================================================

def build_specialist_context(
    user_message: str,
    triage_summary: str = "",
    history: Optional[list[dict]] = None,
    triage_prefix: str = "分诊摘要",
) -> str:
    """
    构建 Specialist Agent 的 user prompt 上下文

    所有 Specialist Agent 共享相同的上下文构建逻辑。

    Args:
        user_message: 客户消息
        triage_summary: 分诊摘要
        history: 对话历史（可选）
        triage_prefix: 分诊摘要前缀（可定制，如 "分诊摘要（注意情感状态）"）

    Returns:
        构建好的上下文文本
    """
    context = f"客户消息：{user_message}"
    if triage_summary:
        context += f"\n\n{triage_prefix}：{triage_summary}"

    if history:
        recent = history[-6:]
        history_lines = [
            f"[{m.get('role', '?')}]: {m.get('content', '')[:150]}"
            for m in recent
        ]
        if history_lines:
            context += f"\n\n对话历史：\n" + "\n".join(history_lines)

    return context
