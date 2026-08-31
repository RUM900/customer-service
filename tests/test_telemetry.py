"""
LLM 可观测性埋点层测试

覆盖:
- LLMCallRecord 数据模型与序列化
- _record_call 兼容 OpenAI(prompt_tokens) / Claude(input_tokens) 两种 usage 字段
- contextvars 隐式传递 trace_id / agent_name
- 环形缓冲区上限与查询
- 成本估算
- 流式调用的 finally 埋点（generator 消费后才落盘）
"""
import pytest

from src.llm.telemetry import (
    LLMCallRecord,
    record_call,
    get_recent_calls,
    reset_records,
    set_trace_id,
    set_agent_name,
    new_trace_id,
    get_trace_id,
    get_agent_name,
    estimate_cost,
)


# ============================================================
# Fake usage 对象 — 模拟 OpenAI / Claude SDK 返回的 usage
# ============================================================

class FakeOpenAIUsage:
    """模拟 OpenAI/DashScope 的 usage 对象"""
    def __init__(self, prompt=100, completion=20):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion


class FakeClaudeUsage:
    """模拟 Claude 的 usage 对象（字段名不同）"""
    def __init__(self, input_tokens=100, output_tokens=20):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


# ============================================================
# fixtures
# ============================================================

@pytest.fixture(autouse=True)
def clean_records():
    """每个测试前后清空缓冲区，避免相互污染"""
    reset_records()
    # 同时重置 contextvars（设回空）
    set_trace_id("")
    set_agent_name("")
    yield
    reset_records()
    set_trace_id("")
    set_agent_name("")


# ============================================================
# LLMCallRecord 数据模型
# ============================================================

class TestLLMCallRecord:
    """测试数据模型本身"""

    def test_to_dict_contains_all_fields(self):
        record = LLMCallRecord(
            trace_id="t1", agent_name="triage", provider="dashscope",
            model="qwen-turbo", call_type="chat",
            prompt_tokens=100, completion_tokens=20, total_tokens=120,
            latency_ms=320.5, success=True,
        )
        d = record.to_dict()
        assert d["trace_id"] == "t1"
        assert d["agent_name"] == "triage"
        assert d["call_type"] == "chat"
        assert d["total_tokens"] == 120
        assert d["success"] is True
        # error 为空时序列化为 None（便于 JSON 输出干净）
        assert d["error"] is None

    def test_to_dict_with_error(self):
        record = LLMCallRecord(
            trace_id="", agent_name="", provider="openai",
            model="gpt-4o", call_type="chat_structured",
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            latency_ms=100.0, success=False, error="timeout",
        )
        d = record.to_dict()
        assert d["success"] is False
        assert d["error"] == "timeout"


# ============================================================
# contextvars 传递
# ============================================================

class TestContextPropagation:
    """测试 trace_id / agent_name 的隐式传递"""

    def test_set_and_get_trace_id(self):
        set_trace_id("trace_abc")
        assert get_trace_id() == "trace_abc"

    def test_new_trace_id_generates_unique(self):
        id1 = new_trace_id()
        id2 = new_trace_id()
        assert id1 != id2
        assert len(id1) == 12
        assert get_trace_id() == id2  # 最后一次 set 生效

    def test_set_and_get_agent_name(self):
        set_agent_name("supervisor")
        assert get_agent_name() == "supervisor"

    def test_default_context_is_empty(self):
        # 重置后默认值是空字符串
        assert get_trace_id() == ""
        assert get_agent_name() == ""


# ============================================================
# _record_call 兼容性（核心：多 Provider usage 字段）
# ============================================================

class TestRecordCallCompat:
    """测试基类 _record_call 对不同 Provider usage 的兼容提取"""

    def test_record_openai_style_usage(self):
        from src.llm.base import LLMProvider

        # 构造一个最小 Provider 实例（绕过抽象方法）
        provider = _make_provider("dashscope", "qwen-turbo")
        usage = FakeOpenAIUsage(prompt=150, completion=30)

        provider._record_call("chat", usage, latency_ms=200.0)

        calls = get_recent_calls(10)
        assert len(calls) == 1
        assert calls[0]["prompt_tokens"] == 150
        assert calls[0]["completion_tokens"] == 30
        assert calls[0]["total_tokens"] == 180
        assert calls[0]["success"] is True

    def test_record_claude_style_usage(self):
        from src.llm.base import LLMProvider

        provider = _make_provider("claude", "claude-3-5-sonnet")
        usage = FakeClaudeUsage(input_tokens=200, output_tokens=50)

        provider._record_call("chat_structured", usage, latency_ms=500.0)

        calls = get_recent_calls(10)
        assert len(calls) == 1
        # Claude 的 input_tokens 应被映射为 prompt_tokens
        assert calls[0]["prompt_tokens"] == 200
        assert calls[0]["completion_tokens"] == 50
        # total_tokens 缺失时自动求和
        assert calls[0]["total_tokens"] == 250

    def test_record_with_none_usage(self):
        """流式调用拿不到 usage，传 None 应记 0"""
        from src.llm.base import LLMProvider

        provider = _make_provider("openai", "gpt-4o")
        provider._record_call("chat_stream", None, latency_ms=100.0)

        calls = get_recent_calls(10)
        assert calls[0]["prompt_tokens"] == 0
        assert calls[0]["completion_tokens"] == 0
        assert calls[0]["total_tokens"] == 0

    def test_record_failed_call(self):
        from src.llm.base import LLMProvider

        provider = _make_provider("dashscope", "qwen-turbo")
        provider._record_call(
            "chat_structured", FakeOpenAIUsage(100, 20), latency_ms=300.0,
            success=False, error="JSON parse failed", retry_count=2,
        )

        calls = get_recent_calls(10)
        assert calls[0]["success"] is False
        assert calls[0]["error"] == "JSON parse failed"
        assert calls[0]["retry_count"] == 2

    def test_record_carries_context(self):
        """_record_call 应隐式带上当前 context 的 trace_id / agent_name"""
        from src.llm.base import LLMProvider

        new_trace_id()
        set_agent_name("triage")
        provider = _make_provider("dashscope", "qwen-turbo")

        provider._record_call("chat", FakeOpenAIUsage(10, 5), latency_ms=50.0)

        calls = get_recent_calls(10)
        assert calls[0]["trace_id"] != ""
        assert calls[0]["trace_id"] == get_trace_id()
        assert calls[0]["agent_name"] == "triage"


# ============================================================
# 环形缓冲区
# ============================================================

class TestRingBuffer:
    """测试内存环形缓冲区行为"""

    def test_recent_calls_ordered_newest_first(self):
        for i in range(5):
            record_call(LLMCallRecord(
                trace_id="t", agent_name="a", provider="p", model="m",
                call_type="chat", prompt_tokens=i, completion_tokens=0,
                total_tokens=i, latency_ms=0, success=True,
            ))
        calls = get_recent_calls(10)
        # 最新（最后插入的 i=4）应排在第一位
        assert calls[0]["prompt_tokens"] == 4
        assert calls[1]["prompt_tokens"] == 3

    def test_limit_parameter(self):
        for i in range(10):
            record_call(LLMCallRecord(
                trace_id="t", agent_name="a", provider="p", model="m",
                call_type="chat", prompt_tokens=i, completion_tokens=0,
                total_tokens=i, latency_ms=0, success=True,
            ))
        calls = get_recent_calls(3)
        assert len(calls) == 3
        # 只返回最近 3 条
        assert calls[0]["prompt_tokens"] == 9
        assert calls[-1]["prompt_tokens"] == 7

    def test_buffer_capacity_limit(self):
        """超过 1000 条自动丢弃最旧的"""
        from src.llm.telemetry import _records, _MAX_RECORDS
        for i in range(_MAX_RECORDS + 50):
            record_call(LLMCallRecord(
                trace_id="t", agent_name="a", provider="p", model="m",
                call_type="chat", prompt_tokens=i, completion_tokens=0,
                total_tokens=i, latency_ms=0, success=True,
            ))
        assert len(_records) == _MAX_RECORDS
        # 最旧的 50 条被丢弃，缓冲区第一条应是 i=50
        assert _records[0].prompt_tokens == 50

    def test_reset_clears_all(self):
        record_call(LLMCallRecord(
            trace_id="t", agent_name="a", provider="p", model="m",
            call_type="chat", prompt_tokens=1, completion_tokens=0,
            total_tokens=1, latency_ms=0, success=True,
        ))
        reset_records()
        assert get_recent_calls(10) == []


# ============================================================
# 成本估算
# ============================================================

class TestCostEstimation:
    """测试成本估算与单价表"""

    def test_qwen_turbo_cost(self):
        record = LLMCallRecord(
            trace_id="", agent_name="", provider="dashscope",
            model="qwen-turbo", call_type="chat",
            prompt_tokens=1000, completion_tokens=1000, total_tokens=2000,
            latency_ms=0, success=True,
        )
        # 1000 * 0.0003 + 1000 * 0.0006 = 0.0009 元
        assert estimate_cost(record) == pytest.approx(0.0009, abs=1e-6)

    def test_unknown_model_falls_back_to_qwen_turbo(self):
        record = LLMCallRecord(
            trace_id="", agent_name="", provider="x",
            model="some-unknown-model", call_type="chat",
            prompt_tokens=1000, completion_tokens=1000, total_tokens=2000,
            latency_ms=0, success=True,
        )
        # 未知模型按 qwen-turbo 计价
        assert estimate_cost(record) == pytest.approx(0.0009, abs=1e-6)

    def test_get_recent_calls_includes_cost(self):
        from src.llm.base import LLMProvider

        provider = _make_provider("dashscope", "qwen-turbo")
        provider._record_call(
            "chat", FakeOpenAIUsage(prompt=1000, completion=1000), latency_ms=0
        )
        calls = get_recent_calls(1)
        assert "cost" in calls[0]
        assert calls[0]["cost"] == pytest.approx(0.0009, abs=1e-6)


# ============================================================
# 流式埋点边界
# ============================================================

class TestStreamTelemetryBoundary:
    """测试流式调用的 finally 埋点边界"""

    @pytest.mark.asyncio
    async def test_stream_records_after_consumption(self):
        """generator 被消费后，finally 埋点应落盘"""
        from src.llm.base import LLMProvider

        provider = _make_provider("dashscope", "qwen-turbo")

        # 构造一个最小 async generator，模拟 provider.chat_stream 的埋点行为
        async def fake_stream():
            start = 0.0
            try:
                yield "hello"
                yield "world"
            finally:
                provider._record_call("chat_stream", None, latency_ms=start)

        # 消费 generator
        chunks = []
        async for c in fake_stream():
            chunks.append(c)

        assert chunks == ["hello", "world"]
        calls = get_recent_calls(10)
        assert len(calls) == 1
        assert calls[0]["call_type"] == "chat_stream"

    @pytest.mark.asyncio
    async def test_stream_not_consumed_no_record(self):
        """generator 未被消费时，finally 不执行，不应有记录"""
        from src.llm.base import LLMProvider

        provider = _make_provider("dashscope", "qwen-turbo")

        async def fake_stream():
            try:
                yield "hello"
            finally:
                provider._record_call("chat_stream", None, latency_ms=0)

        # 只创建不消费
        gen = fake_stream()
        # 不迭代
        assert get_recent_calls(10) == []
        # 手动关闭避免 warning
        await gen.aclose()


# ============================================================
# 辅助
# ============================================================

def _make_provider(name: str, model: str):
    """构造一个可用于测试的最小 Provider（绕过 ABC 限制）"""
    from src.llm.base import LLMProvider

    class _TestProvider(LLMProvider):
        @property
        def provider_name(self):
            return name

        async def chat(self, messages, **kwargs):
            ...

        async def chat_structured(self, messages, response_model, **kwargs):
            ...

        async def chat_stream(self, messages, **kwargs):
            yield ""

    return _TestProvider(model=model, temperature=0.1, max_tokens=1024)
