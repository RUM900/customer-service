"""
对话模型 — 消息、会话等核心数据结构

设计原则：
- 所有模型可 JSON 序列化
- UUID 保证实体可追溯
- 支持多轮对话历史
"""
from datetime import datetime
from enum import Enum
from uuid import uuid4
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# 枚举
# ============================================================

class MessageRole(str, Enum):
    """消息角色"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ConversationStatus(str, Enum):
    """对话状态"""
    ACTIVE = "active"            # 进行中
    RESOLVED = "resolved"        # 已解决
    ESCALATED = "escalated"      # 已升级
    HANDOFF = "handoff"          # 已转人工
    CLOSED = "closed"            # 已关闭
    ERROR = "error"              # 异常


class Tier(str, Enum):
    """Agent 层级"""
    TRIAGE = "triage"
    SPECIALIST = "specialist"
    SUPERVISOR = "supervisor"
    HUMAN = "human"


class ResolutionType(str, Enum):
    """解决方式"""
    FAQ_AUTO = "faq_auto"             # FAQ 自动回答
    AGENT_RESOLVED = "agent_resolved"  # Agent 解决
    SUPERVISOR_RESOLVED = "supervisor_resolved"  # 主管解决
    HUMAN_RESOLVED = "human_resolved"  # 人工解决
    UNRESOLVED = "unresolved"         # 未解决


# ============================================================
# 消息
# ============================================================

class Message(BaseModel):
    """单条对话消息"""
    message_id: str = Field(default_factory=lambda: f"msg_{uuid4().hex[:8]}")
    role: MessageRole = MessageRole.USER
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

    # 元数据
    agent_name: Optional[str] = None     # 哪个 Agent 生成的（assistant 消息）
    tool_calls: list[dict] = []          # 工具调用记录
    metadata: dict = {}                  # 扩展元数据


# ============================================================
# 会话
# ============================================================

class Session(BaseModel):
    """用户会话"""
    session_id: str = Field(default_factory=lambda: f"sess_{uuid4().hex[:12]}")
    customer_id: Optional[str] = None
    status: ConversationStatus = ConversationStatus.ACTIVE
    current_tier: Tier = Tier.TRIAGE
    active_agent: Optional[str] = None
    escalation_count: int = 0          # 累计升级次数
    turn_count: int = 0                # 对话轮数
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict = {}


# ============================================================
# 解决方案
# ============================================================

class Resolution(BaseModel):
    """解决方案"""
    resolution_id: str = Field(default_factory=lambda: f"res_{uuid4().hex[:8]}")
    resolution_type: ResolutionType = ResolutionType.AGENT_RESOLVED
    summary: str = ""                          # 解决方案摘要
    detail: str = ""                           # 详细说明
    action_items: list[str] = []               # 执行的操作
    agent_name: Optional[str] = None           # 解决的 Agent
    customer_satisfied: Optional[bool] = None  # 客户是否满意
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# ============================================================
# Agent 错误
# ============================================================

class AgentError(BaseModel):
    """Agent 执行错误"""
    agent_name: str
    error_type: str
    message: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    recoverable: bool = True
