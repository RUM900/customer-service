"""
用户模型 — 管理员/客户账号

设计说明: role 字段预留 customer，为将来开放终端客户登录做准备。
"""
from datetime import datetime
from enum import Enum
from uuid import uuid4
from typing import Optional

from pydantic import BaseModel, Field


class UserRole(str, Enum):
    """用户角色"""
    ADMIN = "admin"
    CUSTOMER = "customer"


class User(BaseModel):
    """用户账号"""
    user_id: str = Field(default_factory=lambda: f"usr_{uuid4().hex[:8]}")
    username: str
    password_hash: str = ""
    role: UserRole = UserRole.CUSTOMER
    display_name: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    last_login_at: Optional[str] = None


class LoginRequest(BaseModel):
    """登录请求"""
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=72)


class TokenResponse(BaseModel):
    """登录成功响应"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 86400
    user: dict = Field(default_factory=dict)
