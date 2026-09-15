"""
知识库管理 API — 文档上传、查询、删除

端点:
- POST /admin/knowledge/upload     上传文档（PDF/DOCX/MD/TXT/CSV/HTML/PPTX）
- GET  /admin/knowledge/documents  列出已索引文档
- GET  /admin/knowledge/documents/{filename}  获取文档详情
- DELETE /admin/knowledge/documents/{filename}  删除文档索引
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends, Request
from pydantic import BaseModel, Field

from src.api.deps import get_knowledge_store
from src.api.auth import require_admin
from src.api.storage import get_storage
from src.models.conversation import Message, MessageRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/knowledge", tags=["知识库管理"])


# ============================================================
# 审计日志辅助
# ============================================================

def _audit_log(request, action: str, thread_id: str, extra: dict = None):
    """统一管理端审计日志（含操作人、IP）"""
    from src.api.auth import get_bearer_token, decode_token

    actor = "api_key"
    token = get_bearer_token(request)
    if token:
        payload = decode_token(token)
        if payload:
            actor = payload.get("username", "jwt_user")

    ip = request.client.host if request.client else "unknown"
    logger.info(
        "AUDIT: action=%s thread=%s actor=%s ip=%s extra=%s",
        action, thread_id, actor, ip, extra or {},
    )


# ============================================================
# HITL 辅助函数
# ============================================================

async def _update_ticket(store, ticket_id: str, status, assignee: Optional[str] = None, resolution: Optional[str] = None):
    """更新工单状态（DB 优先，内存兜底）"""
    try:
        from src.memory.database import get_session_factory
        from src.memory.ticket_store import TicketStore

        factory = get_session_factory()
        async with factory() as db:
            result = await TicketStore(db).update_status(
                ticket_id, status, assigned_agent=assignee, resolution=resolution,
            )
            if result is not None:
                await db.commit()
                return result
    except Exception as e:
        logger.warning(f"HITL: 工单 DB 更新失败: {e}")

    # 内存兜底：直接改内存工单
    try:
        ticket = await store.get_ticket(ticket_id)
        if ticket is None:
            return None
        ticket.status = status
        if assignee:
            ticket.assigned_agent = assignee
        if resolution:
            ticket.resolution = resolution
        return ticket
    except Exception:
        return None


async def _persist_review_result(
    review: dict,
    final_state: dict,
    approved: bool,
    note: str,
) -> Optional[str]:
    """
    将 HITL 审批后的最终回复持久化回会话历史

    - 优先使用图恢复后的 final_state.final_reply
    - 图恢复失败时，用审核单里的决策数据 fallback 生成回复
    """
    session_id = final_state.get("session_id") or review.get("session_id")
    if not session_id:
        return None

    reply = final_state.get("final_reply") or ""
    status = final_state.get("status") or "resolved"
    agent_name = final_state.get("active_agent") or "supervisor"

    # Fallback：图恢复失败时从审核单生成回复
    if not reply:
        decision = review.get("decision") or {}
        if approved:
            reply = decision.get("reply_to_customer") or "您的请求已处理完成。"
        else:
            reply = (
                f"您的请求已提交人工审核。审核意见：{note or '需进一步核实'}。"
                "我们将在24小时内与您联系。"
            )

    try:
        store = get_storage()
        assistant_msg = Message(
            role=MessageRole.ASSISTANT,
            content=reply,
            agent_name=agent_name,
        )
        await store.save_message(session_id, assistant_msg)
        await store.update_session(session_id, status=status, active_agent=agent_name)
        logger.info(f"HITL: 审批结果已回写会话 {session_id}（approved={approved}）")
        return reply
    except Exception as e:
        logger.error(f"HITL: 审批结果回写失败: {e}")
        return None


# ============================================================
# Schema
# ============================================================

class IngestResponse(BaseModel):
    filename: str
    title: str
    format: str
    chunks: int
    indexed: int
    status: str = "ok"


class DocumentInfo(BaseModel):
    filename: str
    title: str
    format: str
    chunks: int
    indexed: int
    indexed_at: str


class DocumentListResponse(BaseModel):
    total: int
    documents: list[DocumentInfo]


# ============================================================
# 上传端点
# ============================================================

@router.post(
    "/upload",
    response_model=IngestResponse,
    summary="上传文档到知识库",
    description="支持 PDF, DOCX, MD, TXT, CSV, HTML, PPTX。上传后自动解析、分块、向量化入库。",
)
async def upload_document(
    file: UploadFile = File(..., description="文档文件"),
    chunk_size: int = Form(500, description="分块大小（字符数）"),
    overlap: int = Form(50, description="块间重叠（字符数）"),
    _auth: str = Depends(require_admin),
):
    """上传文档 → 自动摄入管道"""
    # 验证格式
    allowed = {".pdf", ".docx", ".md", ".txt", ".csv", ".html", ".htm", ".pptx"}
    suffix = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的格式: {suffix}。支持: {', '.join(allowed)}",
        )

    try:
        from src.knowledge.ingestion import IngestionPipeline
        store = get_knowledge_store()
        pipeline = IngestionPipeline(store)

        # 读取文件内容
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="文件为空")

        # 摄入
        result = await pipeline.ingest_bytes(
            file_bytes=content,
            filename=file.filename,
            chunk_size=chunk_size,
            overlap=overlap,
        )

        logger.info(f"知识库上传: {file.filename} → {result['chunks']} 块, {result['indexed']} 已索引")

        return IngestResponse(
            filename=result["filename"],
            title=result["title"],
            format=result.get("format", suffix),
            chunks=result["chunks"],
            indexed=result["indexed"],
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"文档摄入失败: {e}")
        raise HTTPException(status_code=500, detail=f"摄入失败: {e}")


# ============================================================
# 查询端点
# ============================================================

@router.get(
    "/documents",
    response_model=DocumentListResponse,
    summary="列出已索引文档",
)
async def list_documents(_auth: str = Depends(require_admin)):
    """列出所有已上传并索引的文档"""
    # IngestionPipeline 追踪了已索引文档
    from src.knowledge.ingestion import IngestionPipeline
    pipeline = IngestionPipeline(get_knowledge_store())  # 新实例，_indexed_docs 为空
    # 从 ChromaDB 获取实际数据
    store = get_knowledge_store()
    docs = []
    try:
        if store and store._collection:
            result = store._collection.get()
            if result and result["metadatas"]:
                seen = set()
                for meta in result["metadatas"]:
                    src = meta.get("source", "unknown")
                    if src not in seen:
                        seen.add(src)
                        docs.append(DocumentInfo(
                            filename=src,
                            title=meta.get("title", src),
                            format="unknown",
                            chunks=1,
                            indexed=1,
                            indexed_at="",
                        ))
    except Exception as e:
        logger.warning(f"查询文档列表失败: {e}")

    # 也查内存中的 FAQ 数据
    from src.tools.knowledge_search import KnowledgeSearchTool
    try:
        from src.api.deps import get_tool_registry
        kb = get_tool_registry().get_tool("knowledge_search")
        if kb and kb._faq_data:
            docs.append(DocumentInfo(
                filename="faq_samples.json",
                title="默认 FAQ 数据",
                format="json",
                chunks=len(kb._faq_data),
                indexed=0,  # 关键词检索不需要向量索引
                indexed_at="",
            ))
    except Exception:
        pass

    # 去重
    seen_names = set()
    unique_docs = []
    for d in docs:
        if d.filename not in seen_names:
            seen_names.add(d.filename)
            unique_docs.append(d)

    return DocumentListResponse(total=len(unique_docs), documents=unique_docs)


@router.delete(
    "/documents/{filename}",
    summary="删除文档索引",
)
async def delete_document(filename: str, _auth: str = Depends(require_admin)):
    """从知识库中移除文档的所有索引块"""
    from src.knowledge.ingestion import IngestionPipeline
    store = get_knowledge_store()
    pipeline = IngestionPipeline(store)

    ok = pipeline.remove_document(filename)
    if not ok:
        raise HTTPException(status_code=404, detail=f"未找到文档: {filename}")

    return {"status": "deleted", "filename": filename}


# ============================================================
# HITL 人工审核
# ============================================================

@router.get(
    "/reviews",
    summary="列出所有待审核案例",
)
async def list_reviews(_auth: str = Depends(require_admin)):
    """获取所有等待人工审核的案例"""
    from src.api.review_store import get_pending_reviews
    reviews = await get_pending_reviews()
    return {"total": len(reviews), "reviews": reviews}


@router.get(
    "/reviews/{thread_id}",
    summary="获取审核案例详情",
)
async def get_review(thread_id: str, _auth: str = Depends(require_admin)):
    """获取单个审核案例"""
    from src.api.review_store import get_review
    review = await get_review(thread_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"未找到审核案例: {thread_id}")
    return review


@router.post(
    "/reviews/{thread_id}/approve",
    summary="批准审核",
)
async def approve_review(
    thread_id: str,
    note: str = "",
    assignee: str = "",
    request: Request = None,
    _auth: str = Depends(require_admin),
):
    """
    批准人工审核案例

    - supervisor_decision：恢复图执行，并把最终回复持久化回会话历史
    - human_handoff：工单流转为已指派（assigned），指定坐席（可选）
    """
    from src.api.review_store import approve_review
    review = await approve_review(thread_id, note)
    if review is None:
        raise HTTPException(status_code=404, detail=f"未找到待审核案例: {thread_id}")

    _audit_log(request, "approve", thread_id, {"note": note, "assignee": assignee})
    # 转人工审核：图未暂停，无需恢复执行，只需流转工单
    if review.get("review_type") == "human_handoff":
        ticket_id = review.get("ticket_id")
        if ticket_id:
            try:
                store = get_storage()
                from src.models.customer import TicketStatus
                await _update_ticket(store, ticket_id, TicketStatus.ASSIGNED, assignee=assignee or None)
            except Exception as e:
                logger.warning(f"HITL: 工单流转失败 ticket={ticket_id}: {e}")
        return {"status": "approved", "thread_id": thread_id, "ticket_id": ticket_id}

    # 尝试恢复图执行（supervisor 高风险决策的 interrupt 挂起）
    final_state = {}
    try:
        from src.api.deps import get_graph
        graph = await get_graph()
        from langgraph.types import Command
        config = {"configurable": {"thread_id": thread_id}}
        # 注入批准指令，并捕获恢复后的最终状态
        result = await graph.ainvoke(
            Command(resume={"approved": True, "note": note}),
            config,
        )
        if isinstance(result, dict):
            final_state = result
        logger.info(f"HITL: 图已恢复执行 thread={thread_id}")
    except Exception as e:
        logger.warning(f"HITL: 图恢复失败（可能在另一个进程）: {e}")

    # 将最终回复持久化回会话历史（图恢复失败时用审核单数据 fallback）
    await _persist_review_result(
        review=review,
        final_state=final_state,
        approved=True,
        note=note,
    )

    return {"status": "approved", "thread_id": thread_id}


@router.post(
    "/reviews/{thread_id}/reject",
    summary="驳回审核",
)
async def reject_review(thread_id: str, reason: str = "", request: Request = None, _auth: str = Depends(require_admin)):
    """
    驳回人工审核案例

    - supervisor_decision：图恢复并覆盖为 reject，最终回复回写会话
    - human_handoff：工单关闭（cancelled）
    """
    from src.api.review_store import reject_review
    review = await reject_review(thread_id, reason)
    if review is None:
        raise HTTPException(status_code=404, detail=f"未找到待审核案例: {thread_id}")

    _audit_log(request, "reject", thread_id, {"reason": reason})
    # 转人工审核：图未暂停，无需恢复执行，只需关闭工单
    if review.get("review_type") == "human_handoff":
        ticket_id = review.get("ticket_id")
        if ticket_id:
            try:
                store = get_storage()
                from src.models.customer import TicketStatus
                await _update_ticket(store, ticket_id, TicketStatus.CANCELLED, resolution=reason or "转人工申请未通过")
            except Exception as e:
                logger.warning(f"HITL: 工单关闭失败 ticket={ticket_id}: {e}")
        return {"status": "rejected", "thread_id": thread_id, "reason": reason, "ticket_id": ticket_id}

    # 尝试恢复图执行（supervisor 高风险决策的 interrupt 挂起）
    final_state = {}
    try:
        from src.api.deps import get_graph
        graph = await get_graph()
        from langgraph.types import Command
        config = {"configurable": {"thread_id": thread_id}}
        result = await graph.ainvoke(
            Command(resume={"approved": False, "note": reason}),
            config,
        )
        if isinstance(result, dict):
            final_state = result
        logger.info(f"HITL: 图已恢复(驳回) thread={thread_id}")
    except Exception as e:
        logger.warning(f"HITL: 图恢复失败: {e}")

    # 将最终回复持久化回会话历史（图恢复失败时用审核单数据 fallback）
    await _persist_review_result(
        review=review,
        final_state=final_state,
        approved=False,
        note=reason,
    )

    return {"status": "rejected", "thread_id": thread_id, "reason": reason}


# ============================================================
# 知识库运营闭环（Phase 4）
# ============================================================

@router.get(
    "/gap-analysis",
    summary="知识盲区聚类分析",
)
async def gap_analysis(
    min_count: int = 2,
    _auth: str = Depends(require_admin),
):
    """
    聚类分析 FAQ 未命中/转人工的高频盲区问题

    返回《待补充 FAQ 建议清单》：按 query 聚合 + 出现次数排序 + 建议的 FAQ 模板。
    """
    from src.api.knowledge_gap import get_gap_analysis

    suggestions = await get_gap_analysis(min_count=max(1, min_count))
    return {
        "total_clusters": len(suggestions),
        "suggested_faqs": suggestions,
    }


@router.post(
    "/conflict-check",
    summary="新政策与现有 FAQ 矛盾检测",
)
async def conflict_check(
    policy_text: str = Form(..., description="新上传的政策/文档内容"),
    _auth: str = Depends(require_admin),
):
    """
    用 LLM 对比新政策文本与现有 FAQ，检测矛盾政策

    场景：运营上传新退款政策（如退货期从 7 天改为 14 天）时，
    自动提示与现有 FAQ 冲突的条款，避免知识库自相矛盾。
    """
    if len(policy_text.strip()) < 10:
        raise HTTPException(status_code=400, detail="政策文本过短（至少 10 字）")

    # 拉取现有 FAQ 作为对比基准
    existing_faqs = []
    try:
        from src.memory.database import get_session_factory
        from src.memory.faq_store import FaqStore

        factory = get_session_factory()
        async with factory() as db:
            faqs = await FaqStore(db).list_all()
            existing_faqs = [f.model_dump() for f in faqs][:50]
    except Exception as e:
        logger.warning(f"ConflictCheck: FAQ 拉取失败: {e}")

    if not existing_faqs:
        return {"status": "ok", "conflicts": [], "message": "知识库暂无 FAQ，无需检测"}

    # 用 LLM 对比（示例：通过 Triage Agent 的 chat 能力）
    try:
        from src.agents.triage import TriageAgent

        agent = TriageAgent()
        faq_text = "\n".join(
            f"- Q: {f['question']} → A: {f['answer'][:120]}"
            for f in existing_faqs[:30]
        )
        user_prompt = (
            f"新政策内容：\n{policy_text[:2000]}\n\n"
            f"现有 FAQ 条目：\n{faq_text}\n\n"
            "请检查新政策与现有 FAQ 是否矛盾（如：退货天数不一致、赔付标准冲突、"
            "禁止条款相悖）。如果没有矛盾，只返回 JSON：{'conflicts': []}；"
            "如果有，返回 JSON：{'conflicts': [{'faq_question': '...', 'conflict_detail': '说明矛盾点'}]}"
            "只输出 JSON，不要额外解释。"
        )
        raw = await agent.call_chat(
            system_prompt="你是知识库合规审查助手。只输出合法 JSON。",
            user_prompt=user_prompt,
        )

        import json, re
        m = re.search(r'\{.*\}', raw, re.S)
        if m:
            parsed = json.loads(m.group(0))
            conflicts = parsed.get("conflicts", [])
            return {"status": "ok", "conflicts": conflicts}
        return {"status": "ok", "conflicts": [], "raw": raw[:200]}
    except Exception as e:
        logger.error(f"ConflictCheck: LLM 调用失败: {e}")
        return {"status": "error", "conflicts": [], "message": "LLM 检测暂不可用,请稍后重试"}


# ============================================================
# 坐席 Copilot（Phase 4）
# ============================================================

class CopilotRequest(BaseModel):
    """Copilot 辅助请求"""
    session_id: str = Field(..., description="会话 ID")
    customer_message: str = Field(..., description="客户最新消息")
    agent_draft: str = Field(default="", description="坐席已输入的草稿（可选）")


class CopilotResponse(BaseModel):
    """Copilot 辅助响应"""
    suggested_reply: str = Field(description="推荐回复话术")
    context_summary: str = Field(default="", description="上下文摘要（给坐席看）")
    suggested_tools: list[str] = Field(default_factory=list, description="建议查询的操作卡片")


@router.post(
    "/copilot/assist",
    response_model=CopilotResponse,
    summary="坐席 Copilot：实时推荐话术",
)
async def copilot_assist(
    req: CopilotRequest,
    _auth: str = Depends(require_admin),
):
    """
    人工坐席聊天时，后台 Agent 旁路生成推荐话术

    输入：客户消息 + 坐席草稿（可选）
    输出：推荐回复 + 上下文摘要 + 建议查询的工具
    """
    # 拉取会话历史作为上下文
    history_text = ""
    try:
        store = get_storage()
        history = await store.get_history(req.session_id, limit=10)
        if history:
            history_text = "\n".join(
                f"[{'客户' if m.get('role') == 'user' else '客服'}]: {str(m.get('content', ''))[:150]}"
                for m in history
            )
    except Exception as e:
        logger.warning(f"Copilot: 历史拉取失败: {e}")

    try:
        from src.agents.supervisor import SupervisorAgent

        agent = SupervisorAgent()
        user_prompt = (
            f"## 客户最新消息\n{req.customer_message}\n\n"
            f"## 会话历史\n{history_text or '（无）'}\n\n"
            f"## 坐席草稿\n{req.agent_draft or '（未填写）'}\n\n"
            "请以资深客服主管视角，给出：\n"
            "1. suggested_reply: 一段可以直接发送给客户的、专业且有人情味的回复话术；\n"
            "2. context_summary: 一段给坐席看的上下文摘要（客户诉求、情绪、需要核实的点）；\n"
            "3. suggested_tools: 建议坐席查询的工具列表，如 crm_lookup / order_lookup / knowledge_search / ticket_query。\n"
            "只输出 JSON：{'suggested_reply': '...', 'context_summary': '...', 'suggested_tools': [...]}"
        )
        raw = await agent.call_chat(
            system_prompt="你是客服坐席的 AI 副驾驶。只输出合法 JSON，不要多余文字。",
            user_prompt=user_prompt,
        )

        import json, re
        m = re.search(r'\{.*\}', raw, re.S)
        if m:
            parsed = json.loads(m.group(0))
            return CopilotResponse(
                suggested_reply=parsed.get("suggested_reply", ""),
                context_summary=parsed.get("context_summary", ""),
                suggested_tools=parsed.get("suggested_tools", []),
            )
        return CopilotResponse(
            suggested_reply=req.customer_message, context_summary="无法解析 Copilot 回复",
        )
    except Exception as e:
        logger.error(f"Copilot: 调用失败: {e}")
        raise HTTPException(status_code=500, detail=f"Copilot 暂时不可用: {e}")
