"""
LLM 可观测性 — 埋点数据收集

核心设计:
- 通过 contextvars 隐式传递 trace_id / agent_name，不改变 LLM 返回值类型
- 内存环形缓冲区保存最近 N 条调用记录，供 /admin/traces 查看
- 附成本估算（按模型每千 token 单价），后续可无缝接入 Langfuse

Trace 上下文贯穿:
    API 请求(middleware 生成 trace_id)
      → 图节点(设置 agent_name)
        → Agent 调用 BaseAgent.call_*
          → Provider.chat/chat_structured
            → _record_call 落盘记录
"""
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime

# ============================================================
# Context 传递（隐式，避免改接口返回值）
# ============================================================

_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")
_agent_name_ctx: ContextVar[str] = ContextVar("agent_name", default="")


def set_trace_id(trace_id: str) -> None:
    """设置当前请求的 trace_id"""
    _trace_id_ctx.set(trace_id)


def get_trace_id() -> str:
    return _trace_id_ctx.get()


def new_trace_id() -> str:
    """生成新 trace_id 并设置到当前 context"""
    tid = uuid.uuid4().hex[:12]
    set_trace_id(tid)
    return tid


def set_agent_name(name: str) -> None:
    """设置当前执行的 Agent 名称（图节点入口调用）"""
    _agent_name_ctx.set(name)


def get_agent_name() -> str:
    return _agent_name_ctx.get()


# ============================================================
# 调用记录
# ============================================================

@dataclass
class LLMCallRecord:
    """单次 LLM 调用的可观测数据"""
    trace_id: str
    agent_name: str
    provider: str
    model: str
    call_type: str          # chat / chat_structured / chat_stream
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: float
    success: bool
    error: str = ""
    retry_count: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "agent_name": self.agent_name,
            "provider": self.provider,
            "model": self.model,
            "call_type": self.call_type,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "success": self.success,
            "error": self.error or None,
            "retry_count": self.retry_count,
            "timestamp": self.timestamp,
        }


# ============================================================
# 内存环形缓冲区（最近 1000 条）
# ============================================================

_MAX_RECORDS = 1000
_records: deque[LLMCallRecord] = deque(maxlen=_MAX_RECORDS)


def record_call(record: LLMCallRecord) -> None:
    """记录一次 LLM 调用"""
    _records.append(record)


def get_recent_calls(limit: int = 50) -> list[dict]:
    """最近 N 条调用记录（新→旧），附带成本估算"""
    records = list(_records)[-limit:][::-1]
    return [dict(r.to_dict(), cost=estimate_cost(r)) for r in records]


def reset_records() -> None:
    """清空记录（测试用）"""
    _records.clear()


# ============================================================
# 成本估算（各模型每千 token 单价，人民币元）
# ============================================================

_MODEL_COST_MAP = {
    "qwen-turbo": {"input": 0.0003, "output": 0.0006},
    "qwen-plus": {"input": 0.0008, "output": 0.002},
    "qwen-max": {"input": 0.0024, "output": 0.0096},
    "gpt-4o-mini": {"input": 0.0002, "output": 0.0006},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-5-haiku": {"input": 0.001, "output": 0.005},
}


def estimate_cost(record: LLMCallRecord) -> float:
    """估算单次调用的成本（元），未收录模型按 qwen-turbo 计"""
    cost = _MODEL_COST_MAP.get(record.model, _MODEL_COST_MAP["qwen-turbo"])
    return round(
        record.prompt_tokens / 1000 * cost["input"]
        + record.completion_tokens / 1000 * cost["output"],
        6,
    )