"""
路由函数 — LangGraph 条件分支逻辑

所有路由决策的纯函数，不依赖外部状态。
"""

from src.models.routing import IntentType


# ============================================================
# Triage → 下一步路由
# ============================================================

def route_after_triage(state: dict) -> str:
    """
    Triage 之后的路由决策

    根据 TriageResult 决定去向:
    - FAQ 且置信度高 → faq_answer
    - 各专业领域 → 对应的 specialist
    - 需要立即人工 → human_handoff
    """
    triage = state.get("triage_result")

    if triage is None:
        # Triage 失败，兜底到 technical
        return "technical"

    # 如果需要立即转人工
    if triage.get("requires_immediate_human"):
        return "human_handoff"

    recommended = triage.get("recommended_agent", "technical")

    # 映射到实际的 node 名称
    route_map = {
        "faq_answer": "faq_answer",
        "technical": "technical",
        "billing": "billing",
        "product": "product",
        "complaint": "complaint",
    }

    target = route_map.get(recommended, "technical")

    # 如果是 faq_answer 且置信度低，改为兜底
    if target == "faq_answer":
        confidence = triage.get("intent_confidence", 0)
        if confidence < 0.6:
            return "technical"

    return target


# ============================================================
# Specialist → 下一步路由
# ============================================================

def route_after_specialist(state: dict) -> str:
    """
    Specialist 处理后的路由决策

    优先级:
    1. 有工具待执行 → tools（执行后回到 specialist）
    2. resolved → __end__
    3. needs_escalation → supervisor / human_handoff
    4. 默认 → __end__（追问/澄清场景）
    """
    response = state.get("specialist_response")

    if response is None:
        return "__end__"

    # 工具执行优先：如果有工具待调用且未超过最大轮次
    tools_to_use = response.get("tools_to_use", [])
    if tools_to_use:
        tool_round = state.get("tool_round", 0)
        max_tool_rounds = state.get("max_tool_rounds", 3)
        if tool_round < max_tool_rounds:
            return "tools"

    if response.get("is_resolved"):
        return "__end__"

    if response.get("needs_escalation"):
        esc_count = state.get("escalation_count", 0)
        max_esc = state.get("max_escalation_rounds", 2)

        if esc_count >= max_esc:
            return "human_handoff"

        return "supervisor"

    # 默认：追问/澄清，结束本轮
    return "__end__"


def route_after_tools(state: dict) -> str:
    """
    工具执行后 → 回到同一个 specialist 继续处理

    specialist_agent 记录了当前处理的特工名称。
    """
    agent = state.get("specialist_agent", "technical")
    # 确保返回的是已注册的节点名
    valid = {"technical", "billing", "product", "complaint"}
    return agent if agent in valid else "technical"


# ============================================================
# Supervisor → 下一步路由
# ============================================================

def route_after_supervisor(state: dict) -> str:
    """
    Supervisor 决策后的路由

    - resolve → __end__
    - escalate_to_human → human_handoff
    - coordinate → 回到对应的 specialist（通过 specialist_agent 字段）
    - reject → __end__（supersedes specialist）
    """
    decision = state.get("supervisor_decision")

    if decision is None:
        return "__end__"

    action = decision.get("action", "resolve")

    if action == "escalate_to_human":
        return "human_handoff"

    if action == "coordinate":
        # 跨域协调 -> 去另一个 specialist
        specialist = state.get("specialist_agent", "technical")
        return specialist

    # resolve / reject → 结束
    return "__end__"
