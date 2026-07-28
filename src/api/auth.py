"""
API 认证与鉴权

提供 FastAPI 依赖注入函数，用于保护 API 端点。

方案: API Key + Role-Based 鉴权
- 客户端端点 (/chat, /sessions, /tickets) → X-API-Key header
- 管理端点 (/admin/*) → X-Admin-API-Key header

支持通过配置关闭认证（开发环境）。
"""
import logging
from typing import Optional

from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import config

logger = logging.getLogger(__name__)

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
    验证管理端 API Key

    用于 /admin/knowledge, /admin/reviews 等管理端点。

    Returns:
        验证通过的 Admin API Key 标识

    Raises:
        HTTPException 401: 未提供或无效的 Admin API Key
    """
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
            detail="未提供 Admin API Key。请在 X-Admin-API-Key header 中提供有效的管理员 Key。",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if provided_key != expected_admin_key:
        logger.warning(f"无效的 Admin API Key 尝试: {provided_key[:8]}... (IP: {request.client.host if request.client else 'unknown'})")
        raise HTTPException(
            status_code=401,
            detail="无效的 Admin API Key",
        )

    return "admin_authenticated"


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
