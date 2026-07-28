"""
LLM 抽象层

提供统一的 LLM Provider 接口，支持 DashScope / OpenAI / Claude 三种后端。
"""
from src.llm.base import LLMProvider, LLMFactory
from src.llm.dashscope_provider import DashScopeProvider
from src.llm.openai_provider import OpenAIProvider
from src.llm.claude_provider import ClaudeProvider

__all__ = [
    "LLMProvider",
    "LLMFactory",
    "DashScopeProvider",
    "OpenAIProvider",
    "ClaudeProvider",
]
