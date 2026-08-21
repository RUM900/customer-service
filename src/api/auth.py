"""
API 认证与鉴权

提供 FastAPI 依赖注入函数，用于保护 API 端点。

方案: JWT（管理员账号）+ API Key 双轨
- 管理员端点 (/admin/*) → Authorization: Bearer <JWT>（首选），回落 X-Admin-API-Key
- 客户端端点 (/chat, /sessions, /tickets) → X-API-Key header（开发可关闭）

角色预留: role=customer 已设计，待开放终端客户登录时启用 require_user。
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

import jwt as pyjwt
from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import config

logger = logging.getLogger(__name__)

# ============================================================
# JWT 签发与校验
# ============================================================


def create_token(user_id: str, username: str, role: str) -> str:
    """签发 JWT"""
    now = datetime.utcnow()
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=config.JWT_EXPIRE_HOURS),
    }
    return pyjwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """校验并解析 JWT，无效返回 None"""
    try:
        return pyjwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=[config.JWT_ALGORITHM],
        )
    except Exception:
        return None


def get_bearer_token(request: Request) -> Optional[str]:
    """从 Authorization: Bearer <token> 提取 token"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


# ============================================================
# Bearer Token 方案（可选，用于 JWT 扩展）
# ============================================================

_bearer_scheme = HTTPBearer(auto_error=False)


# ============================================================
# API Key 提取
# ============================================================

def _extract_api_key(request: Request) -> Optional[str]:
    """
    从请求中提取 API Key

    优先级: X-API-Key header > Authorization Bearer token > query param ?api_key=
    """
    # 1. X-API-Key header（推荐方式）
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key

    # 2. Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    # 3. Query parameter（不推荐，仅用于 SSE 等不便设置 header 的场景）
    api_key = request.query_params.get("api_key")
    if api_key:
        return api_key

    return None


def _extract_admin_key(request: Request) -> Optional[str]:
    """提取 Admin API Key"""
    # X-Admin-API-Key header
    admin_key = request.headers.get("X-Admin-API-Key")
    if admin_key:
        return admin_key

    # 如果配置了相同的 key 作为 admin key
    admin_key = request.headers.get("X-API-Key")
    return admin_key


# ============================================================
# FastAPI 依赖注入
# ============================================================

async def require_agent(request: Request) -> str:
    """
    验证客户端 API Key

    用于 /chat, /sessions, /tickets 等客户端端点。

    Returns:
        验证通过的 API Key 标识

    Raises:
        HTTPException 401: 未提供或无效的 API Key
    """
    # 如果认证未启用，跳过
    if not config.AUTH_ENABLED:
        return "auth_disabled"

    # 如果未配置 API_KEY，跳过（开发环境）
    if not config.API_KEY:
        logger.warning("AUTH_ENABLED=true 但 API_KEY 未配置，跳过认证")
        return "api_key_not_configured"

    provided_key = _extract_api_key(request)

    if not provided_key:
        raise HTTPException(
            status_code=401,
            detail="未提供 API Key。请在 X-API-Key header 中提供有效的 API Key。",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if provided_key != config.API_KEY:
        logger.warning(f"无效的 API Key 尝试: {provided_key[:8]}... (IP: {request.client.host if request.client else 'unknown'})")
        raise HTTPException(
            status_code=401,
            detail="无效的 API Key",
        )

    return "agent_authenticated"


async def require_admin(request: Request) -> str:
    """
    验证管理员身份 — 用于 /admin/* 管理端点

    校验顺序:
    1. Authorization: Bearer <JWT>（管理员登录签发，首选）
    2. X-Admin-API-Key / X-API-Key（旧 API Key 方式，回落）

    Returns:
        认证标识

    Raises:
        HTTPException 401: 未提供或无效的凭证
    """
    # 1. JWT 优先（管理员登录后签发，不依赖 AUTH_ENABLED 开关）
    token = get_bearer_token(request)
    if token:
        payload = decode_token(token)
        if payload and payload.get("role") == "admin":
            return "admin_authenticated"
        raise HTTPException(
            status_code=401,
            detail="无效或已过期的管理员令牌，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 2. 回落 API Key 逻辑
    # 如果认证未启用，跳过
    if not config.AUTH_ENABLED:
        return "auth_disabled"

    # Admin key: 优先用 ADMIN_API_KEY，fallback 到 API_KEY
    expected_admin_key = config.ADMIN_API_KEY or config.API_KEY

    if not expected_admin_key:
        logger.warning("AUTH_ENABLED=true 但 ADMIN_API_KEY 和 API_KEY 均未配置，跳过认证")
        return "admin_key_not_configured"

    provided_key = _extract_admin_key(request)

    if not provided_key:
        raise HTTPException(
            status_code=401,
            detail="未提供管理凭证。请使用登录后的 Bearer token，或 X-Admin-API-Key header。",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if provided_key != expected_admin_key:
        logger.warning(f"无效的 Admin API Key 尝试: {provided_key[:8]}... (IP: {request.client.host if request.client else 'unknown'})")
        raise HTTPException(
            status_code=401,
            detail="无效的 Admin API Key",
        )

    return "admin_authenticated"


async def require_user(request: Request) -> str:
    """
    验证任意已登录用户（管理员或客户）— 预留，待客户登录开放后启用

    当前仅校验 JWT 有效性，不做角色限制。
    """
    token = get_bearer_token(request)
    if token:
        payload = decode_token(token)
        if payload and payload.get("sub"):
            return "user_authenticated"
        raise HTTPException(
            status_code=401,
            detail="无效或已过期的令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 无 JWT 时回落 API Key（与 require_agent 一致）
    if not config.AUTH_ENABLED:
        return "auth_disabled"
    return await require_agent(request)


# ============================================================
# 可选认证（不强制，但记录身份）
# ============================================================

async def optional_auth(request: Request) -> Optional[str]:
    """
    可选认证 —— 不强制要求 API Key，但如果提供则验证并记录

    用于希望公开访问但追踪调用来源的场景。
    """
    if not config.AUTH_ENABLED:
        return None

    provided_key = _extract_api_key(request)
    if not provided_key:
        return None

    if not config.API_KEY:
        return None

    if provided_key == config.API_KEY:
        return "agent_authenticated"

    logger.warning(f"可选认证: 无效的 API Key: {provided_key[:8]}...")
    return None
