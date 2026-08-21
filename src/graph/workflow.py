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
import re
from typing import Optional, AsyncGenerator

from langgraph.graph import StateGraph, START, END

from src.state import create_initial_state, CustomerServiceState
from src.models.conversation import (
    Message, MessageRole, ConversationStatus, Tier,
    Resolution, ResolutionType, AgentError,
)
from src.models.routing import Urgency, RoutingDecision
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
    route_after_tools,
)
from src.utils.context import prepare_context

import config

logger = logging.getLogger(__name__)

# ============================================================
# Agent 单例（懒加载）
# ============================================================

_agents: dict[str, object] = {}


def reset_agents():
    """清空 Agent 单例缓存，使模型配置改动在下次请求生效"""
    global _agents
    _agents = {}
    logger.info("Agent 单例缓存已重置")


def _get_agent(name: str):
    """懒加载 Agent 单例"""
    if name not in _agents:
        if name == "triage":
            _agents[name] = TriageAgent()
        elif name == "technical":
            _agents[name] = TechnicalAgent()
        elif name == "billing":
            _agents[name] = BillingAgent()
        elif name == "product":
            _agents[name] = ProductAgent()
        elif name == "complaint":
            _agents[name] = ComplaintAgent()
        elif name == "supervisor":
            _agents[name] = SupervisorAgent()
        else:
            raise ValueError(f"未知 Agent: {name}")
    return _agents[name]


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
        agent = _get_agent("triage")

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
            "final_reply": "抱歉，服务暂时繁忙，请稍后再试。",
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
    triage = state.get("triage_result") or {}

    logger.info(f"[FAQ] 直接回答: '{user_message[:60]}...'")

    try:
        agent = _get_agent("triage")

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
            "final_reply": "抱歉，服务暂时繁忙，请稍后再试。",
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
    triage = state.get("triage_result") or {}
    triage_summary = triage.get("summary", "")
    tool_results = state.get("tool_results", [])
    tool_round = state.get("tool_round", 0)

    logger.info(f"[{agent_name}] 处理: '{user_message[:60]}...' (tool_round={tool_round})")

    # 校验 Agent 名称
    valid_specialists = {"technical", "billing", "product", "complaint"}
    if agent_name not in valid_specialists:
        return {
            "status": ConversationStatus.ERROR.value,
            "errors": [AgentError(
                agent_name=agent_name,
                error_type="UnknownAgent",
                message=f"未知的 Specialist: {agent_name}",
            ).model_dump()],
        }

    try:
        agent = _get_agent(agent_name)

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
                f"不要再请求调用这些工具: {', '.join(executed)}。"
                f"如果数据已足够回答，请务必将 tools_to_use 设为空数组 []，不要重复请求已执行过的工具。\n"
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
            "final_reply": "抱歉，服务暂时繁忙，请稍后再试。",
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

    # 优先使用 LLM 结构化输出的工具参数（可从对话历史提取），缺失时回落自动提取
    args_map: dict[str, dict] = {}
    for tc in specialist_response.get("tool_calls", []) or []:
        if isinstance(tc, dict):
            args_map[tc.get("tool")] = tc.get("args") or {}

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
                args = args_map.get(tool_name)
                if args:
                    result = await tool.execute(**args)
                else:
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
        agent = _get_agent("supervisor")

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
            # 加入审核队列（用每轮独立 thread_id，管理员据此恢复图执行）
            thread_id = state.get("thread_id") or state.get("session_id", "unknown")
            await add_review(thread_id, review_context)
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

    转人工前先加入人工审核队列（review_type=human_handoff）。
    图不暂停，客户立即收到"申请已提交"反馈，管理员从审核队列批准/驳回。
    """
    reason = state.get("escalation_reason", "客户要求转人工")
    session_id = state.get("session_id", "unknown")
    thread_id = state.get("thread_id") or session_id
    logger.warning(f"[HumanHandoff] 转人工申请: {reason[:80]}")

    # 加入人工审核队列（DB 持久化，内存兜底）
    try:
        from src.api.review_store import add_review

        await add_review(thread_id, {
            "session_id": session_id,
            "review_type": "human_handoff",
            "message": f"客户要求转人工: {reason}",
            "review_items": ["客户主动要求人工客服", f"原因: {reason[:200]}"],
        })
    except Exception as e:
        logger.error(f"[HumanHandoff] 审核入队失败: {e}")

    handoff_msg = "您的转人工申请已提交，请稍候，客服主管会尽快处理。"

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
            summary=f"转人工申请待审核: {reason}",
            agent_name="human_handoff",
        ).model_dump(),
    }


# ============================================================
# 构建图
# ============================================================

async def build_customer_service_graph() -> StateGraph:
    """
    构建客服系统 LangGraph 工作流

    根据配置选择 MemorySaver 或 PostgresSaver。
    应在应用启动时调用一次并缓存结果。

    Returns:
        编译好的 StateGraph（async ready）
    """
    from src.graph.checkpointer import get_checkpointer

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
    builder.add_edge("faq_answer", END)

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
    builder.add_edge("human_handoff", END)

    # --- 编译 ---
    # 使用动态 checkpointer（开发: MemorySaver, 生产: PostgresSaver）
    checkpointer = await get_checkpointer()
    graph = builder.compile(checkpointer=checkpointer)

    logger.info(f"客服系统 LangGraph 工作流已编译 (checkpointer: {type(checkpointer).__name__})")
    return graph


# ============================================================
# 便捷执行函数
# ============================================================


def _new_thread_id(session_id: str) -> str:
    """为每轮对话生成独立的 checkpointer thread_id

    原因: messages 通道是 operator.add 累积，若每轮复用同一个 thread 并传入完整历史，
    会被 checkpointer 重复叠加。独立 thread 每轮从输入重建，避免累积；
    同时保留 interrupt() 所需的进程内检查点（HITL 审核在当轮 thread 上恢复）。
    """
    from uuid import uuid4

    return f"{session_id}:{uuid4().hex[:8]}"


def _build_initial_state(
    session_id: str,
    user_message: str,
    customer_id: str = "",
    history_messages: Optional[list[dict]] = None,
    thread_id: Optional[str] = None,
) -> tuple[dict, dict]:
    """构建初始 state 和 thread_config（提取公共模式）"""
    initial_state = create_initial_state(
        session_id=session_id,
        customer_id=customer_id,
        max_escalation_rounds=config.MAX_ESCALATION_ROUNDS,
    )
    initial_state["user_message"] = user_message
    if history_messages:
        initial_state["messages"] = list(history_messages)
    thread_id = thread_id or _new_thread_id(session_id)
    initial_state["thread_id"] = thread_id
    thread_config = {"configurable": {"thread_id": thread_id}}
    return initial_state, thread_config


async def run_customer_service(
    session_id: str,
    user_message: str,
    customer_id: str = "",
    history_messages: Optional[list[dict]] = None,
    graph: Optional[StateGraph] = None,
    thread_id: Optional[str] = None,
) -> dict:
    """
    执行一轮客服对话

    Args:
        session_id: 会话 ID
        user_message: 客户消息
        customer_id: 客户 ID（可选）
        history_messages: 历史消息
        graph: 预编译的 StateGraph 实例（可选，不传则自动获取）
        thread_id: 可选，指定 checkpointer thread（默认每轮生成独立 thread）

    Returns:
        更新后的 state dict
    """
    if graph is None:
        from src.api.deps import get_graph
        graph = await get_graph()

    initial_state, thread_config = _build_initial_state(
        session_id, user_message, customer_id, history_messages, thread_id,
    )

    final_state = await graph.ainvoke(initial_state, thread_config)
    return final_state


async def run_customer_service_stream(
    session_id: str,
    user_message: str,
    customer_id: str = "",
    history_messages: Optional[list[dict]] = None,
    graph: Optional[StateGraph] = None,
    thread_id: Optional[str] = None,
) -> AsyncGenerator[dict, None]:
    """
    流式执行客服对话 — 每个节点完成后 yield state 更新

    Args:
        session_id: 会话 ID
        user_message: 客户消息
        customer_id: 客户 ID
        history_messages: 历史消息
        graph: 预编译的 StateGraph 实例（可选）
        thread_id: 可选，指定 checkpointer thread（默认每轮生成独立 thread）

    Yields:
        每个节点的 state 更新 dict
    """
    if graph is None:
        from src.api.deps import get_graph
        graph = await get_graph()

    initial_state, thread_config = _build_initial_state(
        session_id, user_message, customer_id, history_messages, thread_id,
    )

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
    """根据工具类型自动推断参数并执行"""
    # CRM 查询 — 从消息中提取客户 ID 或使用 state 中的
    if tool_name == "crm_lookup":
        customer_id = state.get("customer_id", "")
        if not customer_id:
            match = re.search(r'cust_\w+', user_message)
            customer_id = match.group(0) if match else ""
        if not customer_id:
            return {"error": "无法识别客户 ID，请提供有效的客户标识", "found": False}
        return await tool.execute(customer_id=customer_id)

    # 订单查询 — 提取订单 ID
    elif tool_name in ("order_lookup", "order_status"):
        match = re.search(r'ord_\w+', user_message)
        order_id = match.group(0) if match else ""
        if not order_id:
            return {"error": "无法识别订单 ID，请提供有效的订单号", "found": False}
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
