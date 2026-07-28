"""
LLM 抽象层测试

测试 LLMFactory、各 Provider 的注册与创建、BaseAgent 的调用。
"""
import pytest

from src.llm.base import LLMProvider, LLMFactory


# ============================================================
# LLMFactory 测试
# ============================================================

class TestLLMFactory:
    """测试 Provider 工厂"""

    def test_all_providers_registered(self):
        """验证三个 Provider 都已自动注册"""
        providers = LLMFactory.list_providers()
        assert "dashscope" in providers
        assert "openai" in providers
        assert "claude" in providers
        assert len(providers) >= 3

    def test_create_dashscope_provider(self):
        """创建 DashScope provider（不需要真实 API key，只验证构造）"""
        provider = LLMFactory.create(
            model="qwen-plus",
            provider_name="dashscope",
            temperature=0.1,
            max_tokens=1024,
        )
        assert isinstance(provider, LLMProvider)
        assert provider.provider_name == "dashscope"
        assert provider.model == "qwen-plus"
        assert provider.temperature == 0.1
        assert provider.max_tokens == 1024

    def test_create_openai_provider(self):
        """创建 OpenAI provider"""
        provider = LLMFactory.create(
            model="gpt-4o",
            provider_name="openai",
        )
        assert provider.provider_name == "openai"
        assert provider.model == "gpt-4o"

    def test_create_claude_provider(self):
        """创建 Claude provider"""
        provider = LLMFactory.create(
            model="claude-sonnet-5-20251001",
            provider_name="claude",
        )
        assert provider.provider_name == "claude"

    def test_create_unknown_provider_raises(self):
        """未知 provider 应抛出 ValueError"""
        with pytest.raises(ValueError, match="未知的 LLM Provider"):
            LLMFactory.create(provider_name="unknown_provider")

    def test_default_provider_from_config(self, monkeypatch):
        """默认使用 config.LLM_PROVIDER"""
        monkeypatch.setattr("config.LLM_PROVIDER", "openai")
        provider = LLMFactory.create()
        assert provider.provider_name == "openai"


# ============================================================
# Provider 接口一致性测试
# ============================================================

class TestProviderInterface:
    """验证所有 Provider 都实现了完整的接口"""

    @pytest.mark.parametrize("provider_name", ["dashscope", "openai", "claude"])
    def test_provider_has_required_methods(self, provider_name):
        """每个 Provider 必须有 chat / chat_structured / chat_stream"""
        provider = LLMFactory.create(
            model="test-model",
            provider_name=provider_name,
        )

        assert hasattr(provider, "chat")
        assert callable(provider.chat)

        assert hasattr(provider, "chat_structured")
        assert callable(provider.chat_structured)

        assert hasattr(provider, "chat_stream")
        assert callable(provider.chat_stream)

    @pytest.mark.parametrize("provider_name", ["dashscope", "openai", "claude"])
    def test_provider_has_provider_name(self, provider_name):
        """每个 Provider 必须暴露 provider_name 属性"""
        provider = LLMFactory.create(
            model="test-model",
            provider_name=provider_name,
        )
        assert isinstance(provider.provider_name, str)
        assert len(provider.provider_name) > 0


# ============================================================
# BaseAgent 测试
# ============================================================

class TestBaseAgent:
    """测试 Agent 基类"""

    def test_base_agent_creation(self):
        """BaseAgent 构造"""
        from src.agents.base import BaseAgent

        agent = BaseAgent(model="test-model", temperature=0.5)
        assert agent._model == "test-model"
        assert agent._temperature == 0.5

    def test_base_agent_default_values(self):
        """BaseAgent 默认值来自 config"""
        from src.agents.base import BaseAgent
        import config

        agent = BaseAgent()
        assert agent._temperature == config.DEFAULT_TEMPERATURE

    def test_base_agent_provider_lazy_load(self, monkeypatch):
        """Provider 是懒加载的"""
        from src.agents.base import BaseAgent

        monkeypatch.setattr("config.LLM_PROVIDER", "dashscope")

        agent = BaseAgent(model="qwen-plus")
        # 构造后 provider 应该是 None
        assert agent._provider is None

        # 访问 provider 属性时才创建
        provider = agent.provider
        assert provider is not None
        assert agent._provider is not None

    def test_base_agent_provider_name_override(self, monkeypatch):
        """可以覆盖 provider name"""
        from src.agents.base import BaseAgent

        monkeypatch.setattr("config.LLM_PROVIDER", "dashscope")

        agent = BaseAgent(provider_name="openai")
        # 懒加载的 provider 应该用 openai
        assert agent._provider_name == "openai"
