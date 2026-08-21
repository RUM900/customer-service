"""
管理后台 API — 登录 / 当前用户 / Agent 模型配置 / FAQ 管理

除 /admin/login 外，均需管理员认证（JWT Bearer 或 X-Admin-API-Key）。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.auth import require_admin, create_token
from src.models.user import LoginRequest, TokenResponse, UserRole
from src.models.knowledge import FAQEntry

import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["管理后台"])


# ============================================================
# 登录
# ============================================================

@router.post("/login", response_model=TokenResponse, summary="管理员登录")
async def login(req: LoginRequest):
    """用户名密码登录，成功签发 JWT"""
    from src.memory.database import get_session_factory
    from src.memory.user import UserStore, verify_password

    async with get_session_factory()() as db:
        try:
            store = UserStore(db)
            user = await store.get_by_username(req.username)
            if user is None or not verify_password(req.password, user.password_hash):
                raise HTTPException(status_code=401, detail="用户名或密码错误")
            if user.role != UserRole.ADMIN:
                raise HTTPException(status_code=403, detail="无权访问管理后台")
            await store.update_last_login(user.user_id)
            await db.commit()
        except HTTPException:
            raise
        except Exception as e:
            await db.rollback()
            logger.error(f"登录失败: {e}")
            raise HTTPException(status_code=500, detail="登录服务异常")

    token = create_token(user.user_id, user.username, user.role.value)
    return TokenResponse(
        access_token=token,
        expires_in=config.JWT_EXPIRE_HOURS * 3600,
        user={
            "user_id": user.user_id,
            "username": user.username,
            "role": user.role.value,
            "display_name": user.display_name,
        },
    )


@router.get("/me", summary="当前管理员信息")
async def me(_auth: str = Depends(require_admin)):
    return {"status": "ok", "role": "admin"}


# ============================================================
# Agent 模型配置
# ============================================================

class ModelUpdateRequest(BaseModel):
    agent_name: str = Field(..., description="Agent 名称")
    model: str = Field(..., min_length=1, max_length=128, description="模型名")


@router.get("/model-config", summary="获取全部 Agent 模型配置")
async def get_model_config(_auth: str = Depends(require_admin)):
    from src.api.model_registry import get_all_models
    return {"models": get_all_models()}


@router.put("/model-config", summary="更新某 Agent 的模型配置")
async def update_model_config(req: ModelUpdateRequest, _auth: str = Depends(require_admin)):
    from src.memory.model_config import AGENT_SLOTS
    if req.agent_name not in AGENT_SLOTS:
        raise HTTPException(
            status_code=400,
            detail=f"未知的 Agent: {req.agent_name}，可选: {AGENT_SLOTS}",
        )

    from src.api.model_registry import update_model, validate_model

    error = await validate_model(req.model)
    if error:
        raise HTTPException(status_code=400, detail=error)

    await update_model(req.agent_name, req.model)
    return {"status": "ok", "agent_name": req.agent_name, "model": req.model}


# ============================================================
# FAQ 管理
# ============================================================

@router.get("/faqs", summary="列出全部 FAQ")
async def list_faqs(_auth: str = Depends(require_admin)):
    from src.memory.database import get_session_factory
    from src.memory.faq_store import FaqStore

    async with get_session_factory()() as db:
        store = FaqStore(db)
        entries = await store.list_all()
    return {"total": len(entries), "faqs": [e.model_dump() for e in entries]}


@router.post("/faqs", summary="新增 FAQ", status_code=201)
async def create_faq(entry: FAQEntry, _auth: str = Depends(require_admin)):
    from src.memory.database import get_session_factory
    from src.memory.faq_store import FaqStore
    from src.api.faq_service import reload_faqs

    async with get_session_factory()() as db:
        try:
            store = FaqStore(db)
            existing = await store.get(entry.faq_id)
            if existing:
                raise HTTPException(status_code=400, detail=f"FAQ 已存在: {entry.faq_id}")
            await store.create(entry)
            await db.commit()
        except HTTPException:
            raise
        except Exception as e:
            await db.rollback()
            logger.error(f"FAQ 创建失败: {e}")
            raise HTTPException(status_code=500, detail=f"FAQ 创建失败: {e}")

    await reload_faqs()
    return {"status": "created", "faq": entry.model_dump()}


@router.put("/faqs/{faq_id}", summary="更新 FAQ")
async def update_faq(faq_id: str, entry: FAQEntry, _auth: str = Depends(require_admin)):
    from src.memory.database import get_session_factory
    from src.memory.faq_store import FaqStore
    from src.api.faq_service import reload_faqs

    entry.faq_id = faq_id
    async with get_session_factory()() as db:
        store = FaqStore(db)
        ok = await store.update(entry)
        await db.commit()
    if not ok:
        raise HTTPException(status_code=404, detail=f"未找到 FAQ: {faq_id}")

    await reload_faqs()
    return {"status": "updated", "faq": entry.model_dump()}


@router.delete("/faqs/{faq_id}", summary="删除 FAQ")
async def delete_faq(faq_id: str, _auth: str = Depends(require_admin)):
    from src.memory.database import get_session_factory
    from src.memory.faq_store import FaqStore
    from src.api.faq_service import reload_faqs

    async with get_session_factory()() as db:
        store = FaqStore(db)
        ok = await store.delete(faq_id)
        await db.commit()
    if not ok:
        raise HTTPException(status_code=404, detail=f"未找到 FAQ: {faq_id}")

    await reload_faqs()
    return {"status": "deleted", "faq_id": faq_id}
