"""
FastAPI 路由 — 客服系统 API 端点

端点:
- POST /chat/{session_id}          发起一轮对话
- GET  /chat/{session_id}/stream   SSE 流式对话
- GET  /chat/{session_id}/history  获取对话历史
- POST /sessions                   创建新会话
- GET  /sessions/{session_id}      获取会话状态
- POST /tickets                    创建工单
- GET  /tickets/{ticket_id}        获取工单
- GET  /health                     健康检查
"""
import logging
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from src.api.schemas import (
    ChatRequest, ChatResponse, CreateSessionRequest,
    SessionResponse, HistoryResponse,
    CreateTicketRequest, TicketResponse,
    HealthResponse,
)
from src.api.storage import get_storage
from src.api.auth import require_agent
from src.api.deps import get_graph
from src.api.security import sanitize_user_input, detect_prompt_injection
from src.models.conversation import (
    Message, MessageRole, ConversationStatus,
)
from src.models.customer import TicketPriority, TicketStatus

import config

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================
# Chat — 核心端点
# ============================================================

@router.post(
    "/chat/{session_id}",
    response_model=ChatResponse,
    summary="发起一轮客服对话",
)
async def chat(session_id: str, req: ChatRequest, _auth: str = Depends(require_agent)) -> ChatResponse:
    """核心对话端点 — 每轮对话执行一次完整的 LangGraph 工作流"""
    try:
        from src.graph.workflow import run_customer_service

        # --- 输入安全检测 ---
        clean_message = sanitize_user_input(req.message)
        injection_result = detect_prompt_injection(clean_message)

        store = get_storage()

        # 获取历史消息
        raw_history = await store.get_history(session_id, limit=config.MAX_HISTORY_TURNS * 2)
        history = raw_history

        # 客户 ID：优先请求体，回落会话绑定
        effective_customer_id = req.customer_id or ""
        if not effective_customer_id:
            session = await store.get_session(session_id)
            if session:
                effective_customer_id = session.customer_id or ""

        # 保存用户消息
        user_msg = Message(role=MessageRole.USER, content=clean_message)
        await store.save_message(session_id, user_msg)

        # 执行工作流
        final_state = await run_customer_service(
            session_id=session_id,
            user_message=clean_message,
            customer_id=effective_customer_id,
            history_messages=history,
        )

        # 提取结果
        reply = final_state.get("final_reply", "")
        status = final_state.get("status", "active")
        agent_name = final_state.get("active_agent", "")
        triage = final_state.get("triage_result", {}) or {}
        resolution = final_state.get("resolution")

        # 保存 Agent 回复
        if reply:
            assistant_msg = Message(
                role=MessageRole.ASSISTANT,
                content=reply,
                agent_name=agent_name,
            )
            await store.save_message(session_id, assistant_msg)

        # 更新会话状态
        await store.update_session(
            session_id,
            status=status,
            active_agent=agent_name,
        )

        errors = final_state.get("errors", []) or []
        if isinstance(errors, list) and errors:
            for err in errors:
                logger.error(f"Agent 错误: {err}")

        # 如果检测到 Prompt Injection，在响应中标记
        if injection_result["suspicious"]:
            errors.append({
                "type": "security_warning",
                "message": "检测到可疑的输入模式",
                "risk_level": injection_result["risk_level"],
            })

        return ChatResponse(
            session_id=session_id,
            reply=reply or "抱歉，处理您的请求时遇到了问题，请稍后再试。",
            agent_name=agent_name or None,
            status=status,
            intent=triage.get("primary_intent"),
            sentiment=triage.get("sentiment"),
            resolution=resolution,
            errors=[e for e in errors if isinstance(e, dict)],
        )

    except Exception as e:
        logger.exception(f"Chat 异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# SSE 流式对话
# ============================================================

@router.get(
    "/chat/{session_id}/stream",
    summary="SSE 流式对话",
)
async def chat_stream(
    session_id: str,
    message: str,
    customer_id: Optional[str] = None,
    _auth: str = Depends(require_agent),
):
    """SSE 流式端点 — 实时推送 Agent 工作进度"""
    from src.graph.workflow import run_customer_service_stream, _new_thread_id

    # 与 POST /chat 一致：输入消毒
    clean_message = sanitize_user_input(message)

    async def event_generator():
        try:
            store = get_storage()
            raw_history = await store.get_history(session_id, limit=config.MAX_HISTORY_TURNS * 2)
            history = raw_history

            # 保存用户消息（与 POST /chat 保持一致）
            user_msg = Message(role=MessageRole.USER, content=clean_message)
            await store.save_message(session_id, user_msg)

            graph = await get_graph()
            # 每轮独立 thread（与 workflow 内部保持一致，aget_state 才能取到最终状态）
            thread_id = _new_thread_id(session_id)
            thread_config = {"configurable": {"thread_id": thread_id}}

            # 客户 ID：优先 query 参数，回落会话绑定
            effective_customer_id = customer_id or ""
            if not effective_customer_id:
                session = await store.get_session(session_id)
                if session:
                    effective_customer_id = session.customer_id or ""

            async for event in run_customer_service_stream(
                session_id=session_id,
                user_message=clean_message,
                customer_id=effective_customer_id,
                history_messages=history,
                graph=graph,
                thread_id=thread_id,
            ):
                yield {
                    "event": "agent_update",
                    "data": json.dumps(event, ensure_ascii=False, default=str),
                }

            # 工作流结束后，从 checkpointer 获取最终状态用于持久化
            final_state = {}
            try:
                snapshot = await graph.aget_state(thread_config)
                if snapshot is not None and snapshot.values:
                    final_state = snapshot.values
            except Exception:
                pass

            reply = final_state.get("final_reply", "") or ""
            status = final_state.get("status", ConversationStatus.ACTIVE.value)
            agent_name = final_state.get("active_agent", "") or ""
            triage = final_state.get("triage_result") or {}

            # 保存 Agent 回复并更新会话（与 POST /chat 保持一致）
            if reply:
                assistant_msg = Message(
                    role=MessageRole.ASSISTANT,
                    content=reply,
                    agent_name=agent_name,
                )
                await store.save_message(session_id, assistant_msg)
            await store.update_session(
                session_id,
                status=status,
                active_agent=agent_name,
            )

            yield {
                "event": "done",
                "data": json.dumps({
                    "status": "completed",
                    "session_id": session_id,
                    "reply": reply,
                    "agent_name": agent_name,
                    "intent": triage.get("primary_intent"),
                    "sentiment": triage.get("sentiment"),
                    "conversation_status": status,
                }, ensure_ascii=False, default=str),
            }

        except Exception as e:
            logger.exception(f"SSE 流异常: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)}, ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())


# ============================================================
# 会话历史
# ============================================================

@router.get(
    "/chat/{session_id}/history",
    response_model=HistoryResponse,
    summary="获取对话历史",
)
async def get_history(session_id: str, _auth: str = Depends(require_agent)) -> HistoryResponse:
    store = get_storage()
    messages = await store.get_history(session_id)
    return HistoryResponse(
        session_id=session_id,
        messages=messages,
        total=len(messages),
    )


# ============================================================
# 会话管理
# ============================================================

@router.post(
    "/sessions",
    response_model=SessionResponse,
    summary="创建新会话",
)
async def create_session(req: CreateSessionRequest, _auth: str = Depends(require_agent)) -> SessionResponse:
    store = get_storage()
    session = await store.create_session(customer_id=req.customer_id)
    return SessionResponse(
        session_id=session.session_id,
        customer_id=session.customer_id,
        status=session.status.value,
        current_tier=session.current_tier.value,
        turn_count=session.turn_count,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    summary="获取会话状态",
)
async def get_session(session_id: str, _auth: str = Depends(require_agent)) -> SessionResponse:
    store = get_storage()
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return SessionResponse(
        session_id=session.session_id,
        customer_id=session.customer_id,
        status=session.status.value if hasattr(session.status, 'value') else str(session.status),
        current_tier=session.current_tier.value if hasattr(session.current_tier, 'value') else str(session.current_tier),
        turn_count=session.turn_count,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


# ============================================================
# 工单
# ============================================================

@router.post(
    "/tickets",
    response_model=TicketResponse,
    summary="创建工单",
)
async def create_ticket(req: CreateTicketRequest, _auth: str = Depends(require_agent)) -> TicketResponse:
    store = get_storage()
    ticket = await store.create_ticket(
        session_id=req.session_id,
        subject=req.subject,
        description=req.description,
        customer_id=req.customer_id,
        priority=req.priority,
    )
    return TicketResponse(
        ticket_id=ticket.ticket_id,
        session_id=ticket.session_id,
        subject=ticket.subject,
        description=ticket.description,
        priority=ticket.priority.value,
        status=ticket.status.value,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


@router.get(
    "/tickets/{ticket_id}",
    response_model=TicketResponse,
    summary="获取工单",
)
async def get_ticket(ticket_id: str, _auth: str = Depends(require_agent)) -> TicketResponse:
    store = get_storage()
    ticket = await store.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="工单不存在")

    return TicketResponse(
        ticket_id=ticket.ticket_id,
        session_id=ticket.session_id,
        subject=ticket.subject,
        description=ticket.description,
        priority=ticket.priority.value,
        status=ticket.status.value,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


# ============================================================
# 健康检查
# ============================================================

@router.get(
    "/health",
    response_model=HealthResponse,
    summary="健康检查",
)
async def health() -> HealthResponse:
    db_status = "disconnected"
    try:
        from src.memory.database import get_engine
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        db_status = "connected"
    except Exception:
        pass

    return HealthResponse(
        status="ok",
        llm_provider=config.LLM_PROVIDER,
        database=db_status,
    )
