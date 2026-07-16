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

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

from src.api.deps import get_knowledge_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/knowledge", tags=["知识库管理"])


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
async def list_documents():
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
async def delete_document(filename: str):
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
async def list_reviews():
    """获取所有等待人工审核的案例"""
    from src.api.review_store import get_pending_reviews
    reviews = get_pending_reviews()
    return {"total": len(reviews), "reviews": reviews}


@router.get(
    "/reviews/{thread_id}",
    summary="获取审核案例详情",
)
async def get_review(thread_id: str):
    """获取单个审核案例"""
    from src.api.review_store import get_review
    review = get_review(thread_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"未找到审核案例: {thread_id}")
    return review


@router.post(
    "/reviews/{thread_id}/approve",
    summary="批准审核",
)
async def approve_review(thread_id: str, note: str = ""):
    """
    批准人工审核案例，图继续执行

    批准后 Supervisor 的决策生效（退款/补偿等）。
    如果图仍在内存中（同一进程），自动恢复执行。
    """
    from src.api.review_store import approve_review, get_review
    review = approve_review(thread_id, note)
    if review is None:
        raise HTTPException(status_code=404, detail=f"未找到待审核案例: {thread_id}")

    # 尝试恢复图执行
    try:
        from src.api.deps import get_graph
        graph = get_graph()
        from langgraph.types import Command
        config = {"configurable": {"thread_id": thread_id}}
        # 注入批准指令
        await graph.ainvoke(
            Command(resume={"approved": True, "note": note}),
            config,
        )
        logger.info(f"HITL: 图已恢复执行 thread={thread_id}")
    except Exception as e:
        logger.warning(f"HITL: 图恢复失败（可能在另一个进程）: {e}")

    return {"status": "approved", "thread_id": thread_id}


@router.post(
    "/reviews/{thread_id}/reject",
    summary="驳回审核",
)
async def reject_review(thread_id: str, reason: str = ""):
    """
    驳回人工审核案例

    驳回后 Supervisor 决策被覆盖，回复客户"需要进一步核实"。
    """
    from src.api.review_store import reject_review, get_review
    review = reject_review(thread_id, reason)
    if review is None:
        raise HTTPException(status_code=404, detail=f"未找到待审核案例: {thread_id}")

    # 尝试恢复图执行
    try:
        from src.api.deps import get_graph
        graph = get_graph()
        from langgraph.types import Command
        config = {"configurable": {"thread_id": thread_id}}
        await graph.ainvoke(
            Command(resume={"approved": False, "note": reason}),
            config,
        )
        logger.info(f"HITL: 图已恢复(驳回) thread={thread_id}")
    except Exception as e:
        logger.warning(f"HITL: 图恢复失败: {e}")

    return {"status": "rejected", "thread_id": thread_id, "reason": reason}
