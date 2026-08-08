"""
BaseAgent — 所有 Agent 的共享基类

提供:
- 多 Provider LLM 调用（通过 LLMFactory 自动选择）
- 结构化输出（JSON mode → Pydantic 校验 → 失败重试）
- 普通对话 / 带历史对话 / 工具调用

所有客服 Agent（Triage / Specialist / Supervisor）继承此类。
"""
import logging
from typing import Optional, Type

from pydantic import BaseModel

from src.llm.base import LLMProvider, LLMFactory
import config

logger = logging.getLogger(__name__)


class BaseAgent:
    """
    Agent 基类 — 所有专业 Agent 继承此类

    核心方法:
    - call_structured(): LLM 结构化输出（JSON → Pydantic，自动重试）
    - call_chat(): LLM 普通文本对话
    - call_with_history(): 带历史的 LLM 对话
    - call_with_tools(): 带工具定义的 LLM 对话

    子类只需:
    1. 设置 system_prompt（类属性或构造函数传入）
    2. 调用 call_structured() 并传入 response_model
    """

    def __init__(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        provider_name: Optional[str] = None,
    ):
        """
        Args:
            model: 模型名，默认用 config 中对应 Agent 的模型
            temperature: 温度参数
            provider_name: LLM Provider，默认用 config.LLM_PROVIDER
        """
        self._model = model
        self._temperature = (
            temperature if temperature is not None else config.DEFAULT_TEMPERATURE
        )
        self._provider_name = provider_name or config.LLM_PROVIDER

        # 懒加载 provider
        self._provider: Optional[LLMProvider] = None

    # ----------------------------------------------------------
    # Provider 懒加载
    # ----------------------------------------------------------

    @property
    def provider(self) -> LLMProvider:
        """懒加载 LLM Provider 实例"""
        if self._provider is None:
            self._provider = LLMFactory.create(
                model=self._model,
                temperature=self._temperature,
                provider_name=self._provider_name,
            )
        return self._provider

    # ----------------------------------------------------------
    # 消息构建辅助
    # ----------------------------------------------------------

    @staticmethod
    def _build_messages(
        system_prompt: str,
        user_prompt: str,
        extra: Optional[list[dict]] = None,
    ) -> list[dict]:
        """构建 LLM 消息列表，提取公共模式"""
        messages = [{"role": "system", "content": system_prompt}]
        if extra:
            messages.extend(extra)
        messages.append({"role": "user", "content": user_prompt})
        return messages

    # ----------------------------------------------------------
    # 结构化输出（JSON mode + Pydantic 校验 + 重试）
    # ----------------------------------------------------------

    async def call_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[BaseModel],
        max_retries: Optional[int] = None,
        extra_messages: Optional[list[dict]] = None,
    ) -> BaseModel:
        """
        LLM 结构化输出 — 核心方法

        流程: 构建消息 → LLM 调用 → JSON 解析 → Pydantic 校验 → 失败重试

        Args:
            system_prompt: 系统提示
            user_prompt: 用户输入
            response_model: 期望的 Pydantic 模型类
            max_retries: 最大重试次数，默认取 config
            extra_messages: 额外的历史消息（可选，插入 system 和 user 之间）

        Returns:
            response_model 的实例

        Raises:
            RuntimeError: 超过最大重试次数仍失败
        """
        max_retries = max_retries or config.MAX_RETRIES
        messages = self._build_messages(system_prompt, user_prompt, extra_messages)

        # 通过 provider 调用（provider 内部处理重试和校验）
        return await self.provider.chat_structured(
            messages=messages,
            response_model=response_model,
            max_retries=max_retries,
        )

    # ----------------------------------------------------------
    # 普通对话（非结构化）
    # ----------------------------------------------------------

    async def call_chat(
        self,
        system_prompt: str,
        user_prompt: str,
        extra_messages: Optional[list[dict]] = None,
    ) -> str:
        """普通 LLM 文本对话"""
        messages = self._build_messages(system_prompt, user_prompt, extra_messages)
        return await self.provider.chat(messages=messages)

    # ----------------------------------------------------------
    # 多轮对话（带历史）
    # ----------------------------------------------------------

    async def call_with_history(
        self,
        system_prompt: str,
        user_prompt: str,
        history: list[dict],
    ) -> str:
        """带对话历史的 LLM 调用"""
        messages = self._build_messages(system_prompt, user_prompt, history)
        return await self.provider.chat(messages=messages)

    # ----------------------------------------------------------
    # 工具调用（Function Calling）
    # ----------------------------------------------------------

    async def call_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict],
        history: Optional[list[dict]] = None,
    ) -> str:
        """
        带工具定义的 LLM 调用（Function Calling）

        注意：当前实现将工具 Schema 注入 system prompt，
        让 LLM 自行判断并输出工具调用指令。
        生产环境应使用 Provider 原生的 Function Calling API。

        Args:
            system_prompt: 系统提示
            user_prompt: 用户输入
            tools: 工具列表（JSON Schema 格式）
            history: 历史消息

        Returns:
            LLM 输出文本（可能包含工具调用指令）
        """
        tools_desc = "\n".join(
            f"- **{t['name']}**: {t.get('description', '')}"
            for t in tools
        )

        enhanced_system = (
            f"{system_prompt}\n\n"
            f"## 可用工具\n{tools_desc}\n\n"
            f"如需使用工具，请在回复中明确指出工具名称和参数。"
        )

        messages = self._build_messages(enhanced_system, user_prompt, history)
        return await self.provider.chat(messages=messages)
