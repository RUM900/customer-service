"""
客服系统 LangGraph 工作流 — 主编排图

Graph 结构:

    START → triage ─┬─ faq_answer ────────────────→ END
                     ├─ technical ─┬─ (resolved) ──→ END
                     ├─ billing ───┤  (escalate)
                     ├─ product ───┤     ↓
                     └─ complaint ─┘  supervisor ─┬─→ END
                                       (handoff)   ↓
                                              human_handoff → END

关键设计:
- 每轮对话执行一次 graph.invoke()，通过 checkpointer 保持状态
- Agent 单例懒加载
- 所有节点纯函数（输入 state，输出 state 更新）
"""
import logging
from typing import Optional, AsyncGenerator

from langgraph.graph import StateGraph, START, END

from src.state import create_initial_state, CustomerServiceState
from src.models.conversation import (
    Message, MessageRole, ConversationStatus, Tier,
    Resolution, ResolutionType, AgentError,
)
from src.models.routing import IntentType, Sentiment, Urgency
from src.agents.triage import TriageAgent
from src.agents.technical import TechnicalAgent
from src.agents.billing import BillingAgent
from src.agents.product import ProductAgent
from src.agents.complaint import ComplaintAgent
from src.agents.supervisor import SupervisorAgent
from src.graph.routing import (
    route_after_triage,
    route_after_specialist,
    route_after_supervisor,
    route_after_faq,
    route_after_handoff,
    route_after_tools,
)
from src.utils.context import prepare_context

import config

logger = logging.getLogger(__name__)

# ============================================================
# Agent 单例（懒加载）
# ============================================================

_triage: Optional[TriageAgent] = None
_technical: Optional[TechnicalAgent] = None
_billing: Optional[BillingAgent] = None
_product: Optional[ProductAgent] = None
_complaint: Optional[ComplaintAgent] = None
_supervisor: Optional[SupervisorAgent] = None


def _get_triage() -> TriageAgent:
    global _triage
    if _triage is None:
        _triage = TriageAgent()
    return _triage


def _get_technical() -> TechnicalAgent:
    global _technical
    if _technical is None:
        _technical = TechnicalAgent()
    return _technical


def _get_billing() -> BillingAgent:
    global _billing
    if _billing is None:
        _billing = BillingAgent()
    return _billing


def _get_product() -> ProductAgent:
    global _product
    if _product is None:
        _product = ProductAgent()
    return _product


def _get_complaint() -> ComplaintAgent:
    global _complaint
    if _complaint is None:
        _complaint = ComplaintAgent()
    return _complaint


def _get_supervisor() -> SupervisorAgent:
    global _supervisor
    if _supervisor is None:
        _supervisor = SupervisorAgent()
    return _supervisor


# ============================================================
# 图节点
# ============================================================

async def triage_node(state: dict) -> dict:
    """
    Triage 节点: 分析客户消息，做出路由决策

    输入: state["user_message"], state["messages"]
    输出: state["triage_result"], state["routing_decision"], state["active_agent"]
    """
    user_message = state.get("user_message", "")
    logger.info(f"[Triage] 分析消息: '{user_message[:80]}...'")

    try:
        agent = _get_triage()

        # Context 窗口管理: 智能截断历史消息
        raw_history = state.get("messages", [])
        context = prepare_context(
            raw_history, "triage",
            user_message=user_message,
        )
        history = _messages_to_history(context)

        result = await agent.triage(
            user_message=user_message,
            history=history,
        )

        # 构建路由决策
        routing = _build_routing_decision(result)

        return {
            "triage_result": result.model_dump(),
            "routing_decision": routing,
            "active_agent": "triage",
            "current_tier": Tier.TRIAGE.value,
            "status": ConversationStatus.ACTIVE.value,
        }

    except Exception as e:
        logger.error(f"[Triage] 失败: {e}")
        return {
            "status": ConversationStatus.ERROR.value,
            "errors": [AgentError(
                agent_name="triage",
                error_type=type(e).__name__,
                message=str(e),
            ).model_dump()],
        }


async def faq_answer_node(state: dict) -> dict:
    """
    FAQ 回答节点: 对简单 FAQ 问题直接给出回答

    使用 Triage Agent 的 summary + LLM 直接回复。
    """
    user_message = state.get("user_message", "")
    triage = state.get("triage_result", {})

    logger.info(f"[FAQ] 直接回答: '{user_message[:60]}...'")

    try:
        agent = _get_triage()

        faq_prompt = (
            "你是一个智能客服助手。请根据对话历史（如有）和当前客户问题，"
            "给出简洁、准确的回答。注意对话历史中客户可能提供了个人信息（如姓名），"
            "请在回复中使用。如果问题涉及复杂技术、账单、投诉等，请建议客户等待专业客服处理。"
        )

        # 构建带历史的上下文
        raw_history = state.get("messages", [])
        history = _messages_to_history(
            prepare_context(raw_history, "faq_answer", system_prompt=faq_prompt, user_message=user_message)
        )

        reply = await agent.call_with_history(
            system_prompt=faq_prompt,
            user_prompt=user_message,
            history=history,
        )

        assistant_msg = Message(
            role=MessageRole.ASSISTANT,
            content=reply,
            agent_name="faq_answer",
        )

        return {
            "messages": [assistant_msg.model_dump()],
            "final_reply": reply,
            "status": ConversationStatus.RESOLVED.value,
            "active_agent": "faq_answer",
            "resolution": Resolution(
                resolution_type=ResolutionType.FAQ_AUTO,
                summary=f"FAQ 自动回答: {triage.get('summary', '')}",
                agent_name="faq_answer",
                customer_satisfied=None,
            ).model_dump(),
        }

    except Exception as e:
        logger.error(f"[FAQ] 失败: {e}")
        return {
            "status": ConversationStatus.ERROR.value,
            "errors": [AgentError(
                agent_name="faq_answer",
                error_type=type(e).__name__,
                message=str(e),
            ).model_dump()],
        }


async def specialist_node(state: dict, agent_name: str) -> dict:
    """
    Specialist 节点（通用）: 处理专业领域问题

    支持 technical / billing / product / complaint。
    如果有上一轮工具执行结果，会注入到 prompt 中供 Agent 参考。
    """
    user_message = state.get("user_message", "")
    triage = state.get("triage_result", {})
    triage_summary = triage.get("summary", "")
    tool_results = state.get("tool_results", [])
    tool_round = state.get("tool_round", 0)

    logger.info(f"[{agent_name}] 处理: '{user_message[:60]}...' (tool_round={tool_round})")

    # 映射 agent_name 到对应的 Agent 类
    agent_map = {
        "technical": _get_technical,
        "billing": _get_billing,
        "product": _get_product,
        "complaint": _get_complaint,
    }

    agent_getter = agent_map.get(agent_name)
    if agent_getter is None:
        return {
            "status": ConversationStatus.ERROR.value,
            "errors": [AgentError(
                agent_name=agent_name,
                error_type="UnknownAgent",
                message=f"未知的 Specialist: {agent_name}",
            ).model_dump()],
        }

    try:
        agent = agent_getter()

        # 构造增强的 user_message（如果有工具执行结果）
        enhanced_message = user_message
        if tool_results:
            results_text = "\n".join(
                f"- **{r.get('tool', 'unknown')}**: {r.get('result', r)}"
                for r in tool_results[-10:]
            )
            executed = {r.get('tool', '') for r in tool_results}
            enhanced_message = (
                f"{user_message}\n\n"
                f"[系统提示] 以下工具已经被调用并返回结果，请直接使用这些数据回复客户，"
                f"不要再请求调用这些工具: {', '.join(executed)}\n"
                f"工具执行结果：\n{results_text}"
            )

        # Context 窗口管理
        raw_history = state.get("messages", [])
        context = prepare_context(
            raw_history, "specialist",
            user_message=enhanced_message,
        )
        history = _messages_to_history(context)

        response = await agent.handle(
            user_message=enhanced_message,
            triage_summary=triage_summary,
            history=history,
        )

        # 构建 assistant 消息
        assistant_msg = Message(
            role=MessageRole.ASSISTANT,
            content=response.reply_to_customer,
            agent_name=agent_name,
            tool_calls=[{"tool": t} for t in response.tools_to_use],
        )

        result = {
            "messages": [assistant_msg.model_dump()],
            "specialist_response": response.model_dump(),
            "specialist_agent": agent_name,
            "active_agent": agent_name,
            "current_tier": Tier.SPECIALIST.value,
        }

        if response.is_resolved:
            result["status"] = ConversationStatus.RESOLVED.value
            result["final_reply"] = response.reply_to_customer
            result["resolution"] = Resolution(
                resolution_type=ResolutionType.AGENT_RESOLVED,
                summary=response.diagnosis,
                detail=response.solution,
                action_items=response.action_items,
                agent_name=agent_name,
            ).model_dump()

        elif response.needs_escalation:
            result["escalation_reason"] = response.escalation_reason
            result["escalation_count"] = state.get("escalation_count", 0) + 1
            result["final_reply"] = response.reply_to_customer

        else:
            # 追问/澄清场景，或者工具调用后未解决
            result["final_reply"] = response.reply_to_customer

        return result

    except Exception as e:
        logger.error(f"[{agent_name}] 失败: {e}")
        return {
            "status": ConversationStatus.ERROR.value,
            "errors": [AgentError(
                agent_name=agent_name,
                error_type=type(e).__name__,
                message=str(e),
            ).model_dump()],
        }


async def technical_node(state: dict) -> dict:
    return await specialist_node(state, "technical")


async def billing_node(state: dict) -> dict:
    return await specialist_node(state, "billing")


async def product_node(state: dict) -> dict:
    return await specialist_node(state, "product")


async def complaint_node(state: dict) -> dict:
    return await specialist_node(state, "complaint")


async def tool_node(state: dict) -> dict:
    """
    工具执行节点: 执行 specialist_response.tools_to_use 中的工具

    从 ToolRegistry 获取工具实例，执行后返回结果。
    支持的工具: crm_lookup, order_lookup, order_status, knowledge_search, human_handoff
    """
    specialist_response = state.get("specialist_response", {})
    tools_to_use = specialist_response.get("tools_to_use", [])
    tool_round = state.get("tool_round", 0)

    logger.info(f"[Tools] 第 {tool_round + 1} 轮执行: {tools_to_use}")

    if not tools_to_use:
        return {"tool_round": tool_round + 1}

    try:
        from src.api.deps import get_tool_registry
        registry = get_tool_registry()

        tool_results = []
        for tool_name in tools_to_use:
            tool = registry.get_tool(tool_name)
            if tool is None:
                logger.warning(f"[Tools] 未找到工具: {tool_name}")
                tool_results.append({
                    "tool": tool_name,
                    "error": f"工具 '{tool_name}' 未注册",
                })
                continue

            try:
                # 根据用户消息自动推断参数
                user_message = state.get("user_message", "")
                result = await _execute_tool_with_auto_args(tool, tool_name, user_message, state)
                tool_results.append({
                    "tool": tool_name,
                    "result": result,
                })
                logger.info(f"[Tools] {tool_name} 执行成功")
            except Exception as e:
                logger.error(f"[Tools] {tool_name} 执行失败: {e}")
                tool_results.append({
                    "tool": tool_name,
                    "error": str(e),
                })

        # 将工具结果作为 tool 消息追加
        tool_msgs = []
        for tr in tool_results:
            tool_msgs.append(
                Message(
                    role=MessageRole.TOOL,
                    content=str(tr.get("result", tr.get("error", "")))[:2000],
                    agent_name=f"tool:{tr['tool']}",
                    metadata=tr,
                ).model_dump()
            )

        return {
            "messages": tool_msgs,
            "tool_results": tool_results,
            "tool_round": tool_round + 1,
        }

    except Exception as e:
        logger.error(f"[Tools] 执行异常: {e}")
        return {
            "tool_round": tool_round + 1,
            "errors": [AgentError(
                agent_name="tools",
                error_type=type(e).__name__,
                message=str(e),
            ).model_dump()],
        }


async def supervisor_node(state: dict) -> dict:
    """
    Supervisor 节点: 审查升级案例，做出最终决策
    """
    escalation_reason = state.get("escalation_reason", "")
    specialist_response = state.get("specialist_response", {})
    specialist_agent = state.get("specialist_agent", "unknown")

    logger.info(f"[Supervisor] 审查来自 {specialist_agent} 的升级: {escalation_reason[:80]}")

    try:
        agent = _get_supervisor()

        # 构建升级上下文
        context = (
            f"升级来源: {specialist_agent}\n"
            f"升级原因: {escalation_reason}\n"
            f"Specialist 诊断: {specialist_response.get('diagnosis', 'N/A')}\n"
            f"Specialist 方案: {specialist_response.get('solution', 'N/A')}\n"
        )

        # Context 窗口管理
        history = _messages_to_history(
            prepare_context(state.get("messages", []), "supervisor")
        )

        decision = await agent.decide(
            escalation_context=context,
            specialist_result=specialist_response,
            history=history,
        )

        # HITL: 高风险决策先挂起，等人工审核
        if decision.require_human_review:
            from langgraph.types import interrupt
            from src.api.review_store import add_review
            review_context = {
                "session_id": state.get("session_id", ""),
                "decision": decision.model_dump(),
                "specialist_agent": specialist_agent,
                "escalation_reason": escalation_reason,
                "message": f"人工审核请求: {decision.reasoning}",
                "review_items": decision.review_items,
            }
            # 加入审核队列
            thread_id = state.get("session_id", "unknown")
            add_review(thread_id, review_context)
            # 挂起执行
            human_decision = interrupt(review_context)
            # 人工审核返回后继续: {"approved": true/false, "note": "..."}
            if human_decision and not human_decision.get("approved", True):
                decision.action = "reject"
                decision.reasoning = f"[人工驳回] {human_decision.get('note', '')}"
                decision.reply_to_customer = (
                    f"您的请求已提交人工审核。审核意见：{human_decision.get('note', '需进一步核实')}。"
                    f"我们将在24小时内与您联系。"
                )

        result = {
            "supervisor_decision": decision.model_dump(),
            "active_agent": "supervisor",
            "current_tier": Tier.SUPERVISOR.value,
        }

        if decision.action in ("resolve", "reject"):
            assistant_msg = Message(
                role=MessageRole.ASSISTANT,
                content=decision.reply_to_customer,
                agent_name="supervisor",
            )

            result["messages"] = [assistant_msg.model_dump()]
            result["final_reply"] = decision.reply_to_customer
            result["status"] = ConversationStatus.RESOLVED.value
            result["resolution"] = Resolution(
                resolution_type=ResolutionType.SUPERVISOR_RESOLVED,
                summary=decision.final_solution,
                detail=f"补偿: {decision.compensation}" if decision.compensation else "",
                agent_name="supervisor",
            ).model_dump()

        elif decision.action == "escalate_to_human":
            result["escalation_reason"] = decision.reasoning
            result["escalation_count"] = state.get("escalation_count", 0) + 1

        return result

    except Exception as e:
        logger.error(f"[Supervisor] 失败: {e}")
        return {
            "status": ConversationStatus.ERROR.value,
            "errors": [AgentError(
                agent_name="supervisor",
                error_type=type(e).__name__,
                message=str(e),
            ).model_dump()],
        }


async def human_handoff_node(state: dict) -> dict:
    """
    人工转接节点: 标记对话转人工处理
    """
    reason = state.get("escalation_reason", "Agent 无法解决")
    logger.warning(f"[HumanHandoff] 转人工: {reason[:80]}")

    handoff_msg = (
        f"您的请求已转接给人工客服。转接原因：{reason}\n"
        f"请稍候，我们的客服人员将尽快为您服务。"
    )

    assistant_msg = Message(
        role=MessageRole.ASSISTANT,
        content=handoff_msg,
        agent_name="human_handoff",
    )

    return {
        "messages": [assistant_msg.model_dump()],
        "final_reply": handoff_msg,
        "status": ConversationStatus.HANDOFF.value,
        "current_tier": Tier.HUMAN.value,
        "active_agent": "human_handoff",
        "resolution": Resolution(
            resolution_type=ResolutionType.HUMAN_RESOLVED,
            summary=f"转人工: {reason}",
            agent_name="human_handoff",
        ).model_dump(),
    }


# ============================================================
# 构建图
# ============================================================

def build_customer_service_graph() -> StateGraph:
    """
    构建客服系统 LangGraph 工作流

    Returns:
        编译好的 StateGraph（async ready）
    """
    builder = StateGraph(CustomerServiceState)

    # --- 注册节点 ---
    builder.add_node("triage", triage_node)
    builder.add_node("faq_answer", faq_answer_node)
    builder.add_node("technical", technical_node)
    builder.add_node("billing", billing_node)
    builder.add_node("product", product_node)
    builder.add_node("complaint", complaint_node)
    builder.add_node("tools", tool_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("human_handoff", human_handoff_node)

    # --- 边 ---

    # START → triage
    builder.add_edge(START, "triage")

    # triage → 条件路由
    builder.add_conditional_edges(
        "triage",
        route_after_triage,
        {
            "faq_answer": "faq_answer",
            "technical": "technical",
            "billing": "billing",
            "product": "product",
            "complaint": "complaint",
            "human_handoff": "human_handoff",
        },
    )

    # faq_answer → END
    builder.add_conditional_edges("faq_answer", route_after_faq, {"__end__": END})

    # 每个 specialist → 条件路由（含工具执行）
    for specialist in ["technical", "billing", "product", "complaint"]:
        builder.add_conditional_edges(
            specialist,
            route_after_specialist,
            {
                "__end__": END,
                "tools": "tools",
                "supervisor": "supervisor",
                "human_handoff": "human_handoff",
            },
        )

    # tools → 回到同一个 specialist
    builder.add_conditional_edges(
        "tools",
        route_after_tools,
        {
            "technical": "technical",
            "billing": "billing",
            "product": "product",
            "complaint": "complaint",
        },
    )

    # supervisor → 条件路由
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "__end__": END,
            "human_handoff": "human_handoff",
            "technical": "technical",
            "billing": "billing",
            "product": "product",
            "complaint": "complaint",
        },
    )

    # human_handoff → END
    builder.add_conditional_edges("human_handoff", route_after_handoff, {"__end__": END})

    # --- 编译 ---
    # 使用 MemorySaver 作为默认 checkpointer（生产环境用 PostgresSaver）
    from langgraph.checkpoint.memory import MemorySaver
    graph = builder.compile(checkpointer=MemorySaver())

    logger.info("客服系统 LangGraph 工作流已编译")
    return graph


# ============================================================
# 便捷执行函数
# ============================================================

async def run_customer_service(
    session_id: str,
    user_message: str,
    customer_id: str = "",
    history_messages: Optional[list[dict]] = None,
) -> dict:
    """
    执行一轮客服对话

    Args:
        session_id: 会话 ID
        user_message: 客户消息
        customer_id: 客户 ID（可选）
        history_messages: 历史消息

    Returns:
        更新后的 state dict
    """
    graph = build_customer_service_graph()

    initial_state = create_initial_state(
        session_id=session_id,
        customer_id=customer_id,
        max_escalation_rounds=config.MAX_ESCALATION_ROUNDS,
    )

    initial_state["user_message"] = user_message

    if history_messages:
        initial_state["messages"] = list(history_messages)

    thread_config = {"configurable": {"thread_id": session_id}}

    final_state = await graph.ainvoke(initial_state, thread_config)
    return final_state


async def run_customer_service_stream(
    session_id: str,
    user_message: str,
    customer_id: str = "",
    history_messages: Optional[list[dict]] = None,
) -> AsyncGenerator[dict, None]:
    """
    流式执行客服对话 — 每个节点完成后 yield state 更新

    Args:
        session_id: 会话 ID
        user_message: 客户消息
        customer_id: 客户 ID
        history_messages: 历史消息

    Yields:
        每个节点的 state 更新 dict
    """
    graph = build_customer_service_graph()

    initial_state = create_initial_state(
        session_id=session_id,
        customer_id=customer_id,
        max_escalation_rounds=config.MAX_ESCALATION_ROUNDS,
    )

    initial_state["user_message"] = user_message

    if history_messages:
        initial_state["messages"] = list(history_messages)

    thread_config = {"configurable": {"thread_id": session_id}}

    async for event in graph.astream(initial_state, thread_config, stream_mode="updates"):
        yield event


# ============================================================
# 工具函数
# ============================================================

def _messages_to_history(messages: list[dict]) -> list[dict]:
    """将 Message 列表转为 LLM 用的 role+content 格式"""
    history = []
    for m in messages:
        if isinstance(m, dict):
            role = m.get("role", "user")
            content = m.get("content", "")
        else:
            role = m.role
            content = m.content
        # 统一转为字符串：处理 Pydantic enum 被 LangGraph 还原为对象的情况
        if hasattr(role, 'value'):
            role = role.value
        role = str(role)
        history.append({"role": role, "content": str(content)})
    return history


async def _execute_tool_with_auto_args(tool, tool_name: str, user_message: str, state: dict) -> dict:
    """
    根据工具类型自动从 user_message 或 state 推断参数并执行

    这是一个简化版实现，生产环境应让 LLM 生成精确的工具调用参数。
    """
    # CRM 查询 — 从消息中提取客户 ID 或使用 state 中的
    if tool_name == "crm_lookup":
        customer_id = state.get("customer_id", "")
        if not customer_id:
            # 尝试从消息中提取: "cust_xxx" 格式
            import re
            match = re.search(r'cust_\w+', user_message)
            customer_id = match.group(0) if match else "cust_001"
        return await tool.execute(customer_id=customer_id)

    # 订单查询 — 提取订单 ID
    elif tool_name in ("order_lookup", "order_status"):
        import re
        match = re.search(r'ord_\w+', user_message)
        order_id = match.group(0) if match else "ord_001"
        return await tool.execute(order_id=order_id)

    # 知识库搜索 — 直接用客户消息
    elif tool_name == "knowledge_search":
        return await tool.execute(query=user_message)

    # 工单创建 — 需要 session_id
    elif tool_name == "ticket_create":
        return await tool.execute(
            session_id=state.get("session_id", "unknown"),
            customer_id=state.get("customer_id", ""),
            subject=user_message[:100],
            description=user_message[:500],
        )

    # 人工转接
    elif tool_name == "human_handoff":
        return await tool.execute(
            session_id=state.get("session_id", "unknown"),
            reason=state.get("escalation_reason", "客户要求"),
            summary=user_message[:200],
        )

    # 默认
    else:
        return await tool.execute()


def _build_routing_decision(triage_result) -> dict:
    """从 TriageResult 构建路由决策"""
    from src.models.routing import RoutingDecision

    intent = triage_result.primary_intent
    route = triage_result.recommended_agent

    return RoutingDecision(
        target_node=route,
        reason=f"intent={intent.value}, sentiment={triage_result.sentiment.value}, urgency={triage_result.urgency.value}",
        escalation_reason=(
            f"客户情感: {triage_result.sentiment.value}, 紧急度: {triage_result.urgency.value}"
            if triage_result.urgency in (Urgency.HIGH, Urgency.CRITICAL)
            else None
        ),
        requires_clarification=(triage_result.intent_confidence < 0.5),
    ).model_dump()
