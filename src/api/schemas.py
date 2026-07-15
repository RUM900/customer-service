"""
API Schema — 请求/响应模型

与内部 Pydantic 模型解耦，API 层有自己的 Schema。
"""
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# 请求
# ============================================================

class ChatRequest(BaseModel):
    """POST /chat/{session_id} 请求体"""
    message: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="客户消息",
        examples=["我的订单 #12345 还没收到！"],
    )
    customer_id: Optional[str] = Field(
        default=None,
        description="客户 ID（可选）",
    )


class CreateSessionRequest(BaseModel):
    """POST /sessions 请求体"""
    customer_id: Optional[str] = Field(
        default=None,
        description="客户 ID（可选）",
    )
    metadata: dict = Field(
        default_factory=dict,
        description="会话元数据",
    )


class CreateTicketRequest(BaseModel):
    """POST /tickets 请求体"""
    session_id: str = Field(..., description="关联的会话 ID")
    customer_id: str = Field(default="", description="客户 ID")
    subject: str = Field(..., min_length=1, max_length=500, description="工单主题")
    description: str = Field(..., min_length=1, max_length=5000, description="工单描述")
    priority: str = Field(default="medium", description="优先级: low|medium|high|critical")


# ============================================================
# 响应
# ============================================================

class ChatResponse(BaseModel):
    """单轮对话响应"""
    session_id: str
    reply: str = Field(description="客服回复")
    agent_name: Optional[str] = Field(default=None, description="处理的 Agent")
    status: str = Field(description="对话状态")
    intent: Optional[str] = Field(default=None, description="识别的意图")
    sentiment: Optional[str] = Field(default=None, description="情感标签")
    resolution: Optional[dict] = Field(default=None, description="解决方案")
    errors: list[dict] = Field(default_factory=list)


class SessionResponse(BaseModel):
    """会话信息响应"""
    session_id: str
    customer_id: Optional[str]
    status: str
    current_tier: str
    turn_count: int
    created_at: str
    updated_at: str


class HistoryResponse(BaseModel):
    """对话历史响应"""
    session_id: str
    messages: list[dict]
    total: int


class TicketResponse(BaseModel):
    """工单响应"""
    ticket_id: str
    session_id: str
    subject: str
    description: str
    priority: str
    status: str
    created_at: str
    updated_at: str


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = "ok"
    version: str = "0.1.0"
    llm_provider: str = ""
    database: str = "disconnected"


class ErrorResponse(BaseModel):
    """错误响应"""
    error: str
    detail: Optional[str] = None
    session_id: Optional[str] = None
