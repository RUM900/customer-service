"""
坐席工作台 API — 会话队列 / 会话详情 / 坐席回复 / Copilot / SSE 实时订阅

坐席（role=agent）与管理员（role=admin）均可访问（require_staff）。
区别于管理后台：此模块专注"处理客户会话"这一条业务链路。

端点:
- GET    /agent/conversations                 会话队列（按状态筛选+角标统计）
- GET    /agent/conversations/{session_id}    会话详情（历史+客户画像+状态）
- POST   /agent/conversations/{session_id}/reply  坐席发送回复（直接入库，不重跑图）
- GET    /agent/conversations/{session_id}/stream   SSE 实时订阅（客户新消息/状态变化）
- POST   /agent/conversations/{session_id/copilot   Copilot 辅助（复用管理员端点能力）
"""
import asyncio
import json
import logging
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from src.api.auth import require_staff
from src.api.storage import get_storage
from src.models.conversation import Message, MessageRole, ConversationStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["坐席工作台"])


# ============================================================
# 进程内事件总线（会话级 SSE 订阅）
# ============================================================

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)


async def subscribe_session(session_id: str) -> asyncio.Queue:
    """订阅某会话的事件流，返回队列（调用方负责 finally unsubscribe）"""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers[session_id].add(q)
    return q


def unsubscribe_session(session_id: str, q: asyncio.Queue) -> None:
    _subscribers[session_id].discard(q)
    if not _subscribers[session_id]:
        _subscribers.pop(session_id, None)


async def publish_session_event(session_id: str, event: str, data: dict) -> None:
    """向某会话的所有订阅者广播事件（坐席页实时刷新）"""
    msg = json.dumps(data, ensure_ascii=False, default=str)
    for q in list(_subscribers.get(session_id, ())):
        try:
            q.put_nowait((event, msg))
        except asyncio.QueueFull:
            logger.warning(f"事件队列已满，丢弃: {session_id}/{event}")


# ============================================================
# 请求/响应模型
# ============================================================

class AgentReplyRequest(BaseModel):
    """坐席回复请求"""
    content: str = Field(..., min_length=1, max_length=4000, description="回复内容")
    mark_resolved: bool = Field(default=False, description="回复后是否将会话标记为已解决")


class CopilotRequest(BaseModel):
    """Copilot 辅助请求"""
    session_id: str
    customer_message: str = Field(..., description="客户最新消息")
    agent_draft: str = Field(default="", description="坐席草稿（可选）")


# ============================================================
# 会话队列
# ============================================================

@router.get("/conversations", summary="坐席会话队列")
async def list_conversations(
    status: Optional[str] = None,
    limit: int = 50,
    _auth: str = Depends(require_staff),
):
    """会话队列：默认返回待人工处理（handoff + awaiting_review），可按状态过滤"""
    from src.memory.database import get_session_factory
    from src.memory.session import SessionStore

    # 默认状态：待坐席介入的会话
    if not status:
        statuses = [ConversationStatus.HANDOFF.value, ConversationStatus.AWAITING_REVIEW.value]
        status = None  # 拉全部再过滤
    else:
        statuses = None  # 显式指定状态时不做二次过滤

    try:
        async with get_session_factory()() as db:
            store = SessionStore(db)
            sessions = await store.list_by_status(None, limit=limit)
            handoff_count = await store.count_by_status(ConversationStatus.HANDOFF.value)
            review_count = await store.count_by_status(ConversationStatus.AWAITING_REVIEW.value)
    except Exception as e:
        logger.warning(f"会话队列 DB 读取失败: {e}")
        # 内存兜底
        store = get_storage()
        sessions = []
        handoff_count = review_count = 0

    # 拉取每会话最后一条消息作为摘要
    result = []
    for s in sessions:
        if statuses and s.status.value not in statuses:
            continue
        last_msg = ""
        try:
            history = await get_storage().get_history(s.session_id, limit=1)
            if history:
                last_msg = str(history[-1].get("content", ""))[:80]
        except Exception:
            pass
        result.append({
            "session_id": s.session_id,
            "customer_id": s.customer_id,
            "status": s.status.value,
            "active_agent": s.active_agent,
            "last_message": last_msg,
            "updated_at": s.updated_at,
        })

    return {
        "total": len(result),
        "badges": {"handoff": handoff_count, "awaiting_review": review_count},
        "conversations": result,
    }


# ============================================================
# 会话详情
# ============================================================

@router.get("/conversations/{session_id}", summary="会话详情")
async def conversation_detail(
    session_id: str,
    _auth: str = Depends(require_staff),
):
    """会话详情：历史消息 + 客户画像 + 会话状态（坐席打开会话时加载）"""
    store = get_storage()
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")

    history = await store.get_history(session_id, limit=50)

    # 客户画像（可选加载）
    profile = {}
    if session.customer_id:
        try:
            from src.tools.crm import CRMLookupTool
            result = await CRMLookupTool().execute(customer_id=session.customer_id)
            if result.get("found"):
                c = result["customer"]
                profile = {
                    "name": c.get("name", ""),
                    "tier": c.get("tier", ""),
                    "total_spent": c.get("total_spent", 0),
                    "total_orders": c.get("total_orders", 0),
                    "tags": c.get("tags", []),
                }
        except Exception as e:
            logger.warning(f"客户画像加载失败: {e}")

    return {
        "session_id": session.session_id,
        "customer_id": session.customer_id,
        "status": session.status.value,
        "current_tier": session.current_tier.value,
        "customer_profile": profile,
        "messages": history,
    }


# ============================================================
# 坐席回复
# ============================================================

@router.post("/conversations/{session_id}/reply", summary="坐席发送回复")
async def agent_reply(
    session_id: str,
    req: AgentReplyRequest,
    _auth: str = Depends(require_staff),
):
    """坐席回复：直接写入会话历史并广播，不重跑 LangGraph（避免把人工回复当客户问题）"""
    store = get_storage()
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")

    msg = Message(
        role=MessageRole.ASSISTANT,
        content=req.content,
        agent_name="human",  # 固定标识：人工坐席回复
    )
    await store.save_message(session_id, msg)

    if req.mark_resolved:
        await store.update_session(
            session_id,
            status=ConversationStatus.RESOLVED.value,
            active_agent="human",
        )

    # 广播给该会话的 SSE 订阅者
    await publish_session_event(session_id, "agent_reply", {
        "session_id": session_id,
        "content": req.content,
        "agent_name": "human",
    })

    return {"status": "sent", "message_id": msg.message_id}


# ============================================================
# SSE 实时订阅（坐席侧）
# ============================================================

@router.get("/conversations/{session_id}/stream", summary="坐席 SSE 实时订阅")
async def conversation_stream(
    session_id: str,
    _auth: str = Depends(require_staff),
):
    """SSE 实时推送：客户新消息、坐席回复、会话状态变化"""

    async def event_generator():
        q = await subscribe_session(session_id)
        try:
            # 1. 先发送当前状态快照（便于前端初始化）
            store = get_storage()
            session = await store.get_session(session_id)
            yield {
                "event": "init",
                "data": json.dumps({
                    "session_id": session_id,
                    "status": session.status.value if session else "unknown",
                }, ensure_ascii=False),
            }
            # 2. 持续推送增量事件
            while True:
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=25)
                    yield {"event": event, "data": data}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}  # 心跳保活
        finally:
            unsubscribe_session(session_id, q)

    return EventSourceResponse(event_generator())


# ============================================================
# Copilot（坐席侧入口）
# ============================================================

@router.post("/conversations/{session_id}/copilot", summary="坐席 Copilot 建议")
async def agent_copilot(
    session_id: str,
    req: CopilotRequest,
    _auth: str = Depends(require_staff),
):
    """坐席侧 Copilot：生成推荐话术（复用后端 Copilot 能力）"""
    from src.api.admin_routes import CopilotRequest as AdminCopilotRequest
    from src.api.admin_routes import CopilotResponse, copilot_assist

    core = await copilot_assist(
        AdminCopilotRequest(
            session_id=req.session_id,
            customer_message=req.customer_message,
            agent_draft=req.agent_draft,
        ),
        _auth="staff_authenticated",
    )
    # 自动透传所有字段（避免手动逐个透传导致漏字段——已发生过 3 次）
    return CopilotResponse(**core.model_dump())