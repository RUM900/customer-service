"""
LLM Provider 抽象层

提供统一的 LLM 调用接口，支持多 Provider 切换：
- DashScope (阿里云，OpenAI-compatible)
- OpenAI
- Claude (Anthropic)

每个 Provider 实现：
- chat(): 普通文本对话
- chat_structured(): 结构化输出（JSON → Pydantic）
- chat_stream(): 异步流式输出
"""
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional, Type

from pydantic import BaseModel

import config


class LLMProvider(ABC):
    """LLM Provider 抽象基类 — 所有 Provider 必须实现此接口"""

    def __init__(self, model: str, temperature: float, max_tokens: int):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    @abstractmethod
    async def chat(self, messages: list[dict], **kwargs) -> str:
        """普通对话，返回纯文本"""
        ...

    @abstractmethod
    async def chat_structured(
        self,
        messages: list[dict],
        response_model: Type[BaseModel],
        **kwargs,
    ) -> BaseModel:
        """结构化对话，返回 Pydantic 模型实例"""
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """流式对话，逐 token yield"""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider 名称标识"""
        ...


class LLMFactory:
    """
    LLM Provider 工厂

    根据 config.LLM_PROVIDER 创建对应的 Provider 实例。
    支持运行时切换 Provider，无需重启服务。

    使用方式:
        provider = LLMFactory.create(model="qwen-plus")
        response = await provider.chat([{"role": "user", "content": "Hello"}])
    """

    _providers: dict[str, Type[LLMProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_cls: Type[LLMProvider]) -> None:
        """注册一个新的 Provider 类型"""
        cls._providers[name] = provider_cls

    @classmethod
    def create(
        cls,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        provider_name: Optional[str] = None,
    ) -> LLMProvider:
        """
        创建 LLM Provider 实例

        Args:
            model: 模型名，默认用 config 中对应 Agent 的模型
            temperature: 温度参数
            max_tokens: 最大 token 数
            provider_name: 强制指定 provider，默认用 config.LLM_PROVIDER

        Returns:
            LLMProvider 实例
        """
        name = provider_name or config.LLM_PROVIDER
        temp = temperature if temperature is not None else config.DEFAULT_TEMPERATURE
        tokens = max_tokens or config.MAX_TOKENS

        if name not in cls._providers:
            raise ValueError(
                f"未知的 LLM Provider: '{name}'。"
                f"已注册: {list(cls._providers.keys())}"
            )

        provider_cls = cls._providers[name]

        # 如果没传 model，用默认值
        if model is None:
            model = "qwen-plus"  # 通用默认值，可在 Agent 层覆盖

        return provider_cls(model=model, temperature=temp, max_tokens=tokens)

    @classmethod
    def list_providers(cls) -> list[str]:
        """列出所有已注册的 Provider"""
        return list(cls._providers.keys())


# ============================================================
# 自动注册所有 Provider
# ============================================================

def _register_providers():
    """延迟导入并注册所有 Provider"""
    from src.llm.dashscope_provider import DashScopeProvider
    from src.llm.openai_provider import OpenAIProvider
    from src.llm.claude_provider import ClaudeProvider

    LLMFactory.register("dashscope", DashScopeProvider)
    LLMFactory.register("openai", OpenAIProvider)
    LLMFactory.register("claude", ClaudeProvider)


# 模块加载时自动注册
_register_providers()
