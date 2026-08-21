"""
客服系统状态定义 — LangGraph 全局 State

使用 TypedDict + Annotated reducer 确保状态正确累积:
- messages / errors: operator.add 自动追加
- 其他字段: 默认 reducer（last-writer-wins），缺失时不覆盖
"""
import operator
from typing import Annotated, Optional, TypedDict


class CustomerServiceState(TypedDict, total=False):
    """客服系统 LangGraph 全局状态"""

    # === 会话标识 ===
    session_id: str
    customer_id: Optional[str]
    user_message: str
    thread_id: str  # 每轮对话的独立 checkpointer thread（避免跨轮消息重复累积）

    # === 客户上下文（每轮自动从 CRM 加载） ===
    customer_context: Optional[dict]

    # === 会话记忆（超长对话由 memory_node 生成的 LLM 摘要） ===
    memory_summary: str

    # === 消息（operator.add 累积） ===
    messages: Annotated[list[dict], operator.add]

    # === 分诊 ===
    triage_result: Optional[dict]
    routing_decision: Optional[dict]

    # === Specialist 响应 ===
    specialist_response: Optional[dict]
    specialist_agent: str

    # === 升级 ===
    escalation_count: int
    max_escalation_rounds: int
    escalation_reason: str
    supervisor_decision: Optional[dict]

    # === 解决 ===
    resolution: Optional[dict]
    final_reply: str

    # === 状态 ===
    status: str
    current_tier: str
    active_agent: str

    # === 工具执行 ===
    tool_results: Annotated[list[dict], operator.add]  # 工具执行结果累积
    tool_round: int                                      # 当前工具执行轮次
    max_tool_rounds: int                                 # 最大工具轮次（防无限循环）

    # === 错误（operator.add 累积） ===
    errors: Annotated[list[dict], operator.add]


# ============================================================
# 工厂函数
# ============================================================

def create_initial_state(
    session_id: str,
    customer_id: Optional[str] = None,
    max_escalation_rounds: int = 2,
) -> dict:
    """
    创建新对话的初始 State

    Args:
        session_id: 会话 ID
        customer_id: 可选的客户 ID
        max_escalation_rounds: 最大升级次数

    Returns:
        初始 state dict
    """
    from src.models.conversation import ConversationStatus, Tier

    return {
        "session_id": session_id,
        "customer_id": customer_id,
        "messages": [],
        "user_message": "",
        "customer_context": None,
        "memory_summary": "",
        "triage_result": None,
        "routing_decision": None,
        "specialist_response": None,
        "specialist_agent": "",
        "escalation_count": 0,
        "max_escalation_rounds": max_escalation_rounds,
        "escalation_reason": "",
        "supervisor_decision": None,
        "resolution": None,
        "final_reply": "",
        "status": ConversationStatus.ACTIVE.value,
        "current_tier": Tier.TRIAGE.value,
        "active_agent": "",
        "tool_results": [],
        "tool_round": 0,
        "max_tool_rounds": 2,
        "errors": [],
    }
