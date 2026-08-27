"""
Claude Provider — Anthropic Claude API

使用 Anthropic Python SDK 调用 Claude 模型。
注意：Claude 的 Messages API 与 OpenAI Chat Completions 格式不同，
此 Provider 负责格式转换。

文档: https://docs.anthropic.com/en/api/messages
"""
import json
import time
from typing import AsyncGenerator, Type

from pydantic import BaseModel, ValidationError

from src.llm.base import LLMProvider
from src.llm.format_utils import inject_format_guide
from src.llm.retry import llm_retry
import config


class ClaudeProvider(LLMProvider):
    """Anthropic Claude Provider"""

    def __init__(self, model: str, temperature: float, max_tokens: int):
        super().__init__(model, temperature, max_tokens)
        self._client = None  # 懒加载，避免必须安装 anthropic

    def _get_client(self):
        """懒加载 Anthropic 客户端"""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError:
                raise ImportError(
                    "使用 Claude Provider 需要安装 anthropic SDK: "
                    "pip install anthropic"
                )
            self._client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        return self._client

    @property
    def provider_name(self) -> str:
        return "claude"

    # ----------------------------------------------------------
    # 消息格式转换：OpenAI → Claude
    # ----------------------------------------------------------

    @staticmethod
    def _convert_messages(messages: list[dict]) -> tuple[str, list[dict]]:
        """
        将 OpenAI 格式消息转换为 Claude 格式。

        Claude 要求:
        - 独立的 system 参数（不是 message）
        - messages 中 role 为 "user" / "assistant"
        - 不支持 "system" role 在 messages 中
        """
        system_prompt = ""
        claude_messages = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_prompt += content + "\n"
            elif role in ("user", "assistant"):
                claude_messages.append({"role": role, "content": content})
            else:
                # 兜底：把其他 role 当作 user
                claude_messages.append({"role": "user", "content": content})

        return system_prompt.strip(), claude_messages

    # ----------------------------------------------------------
    # 普通对话
    # ----------------------------------------------------------

    @llm_retry
    async def chat(self, messages: list[dict], **kwargs) -> str:
        system_prompt, claude_msgs = self._convert_messages(messages)
        start = time.time()
        response = await self._get_client().messages.create(
            model=self.model,
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
            system=system_prompt or None,
            messages=claude_msgs,
            temperature=kwargs.get("temperature", self.temperature),
        )
        latency_ms = (time.time() - start) * 1000
        self._record_call("chat", response.usage, latency_ms)
        if not response.content:
            raise RuntimeError("Claude 返回了空的 content 列表")
        return response.content[0].text

    # ----------------------------------------------------------
    # 结构化输出
    # ----------------------------------------------------------

    @llm_retry
    async def chat_structured(
        self,
        messages: list[dict],
        response_model: Type[BaseModel],
        **kwargs,
    ) -> BaseModel:
        max_retries = kwargs.get("max_retries", config.MAX_RETRIES)
        schema = response_model.model_json_schema()

        # 注入格式指南
        msgs = list(messages)
        inject_format_guide(msgs, schema)

        for attempt in range(1, max_retries + 1):
            start = time.time()
            system_prompt, claude_msgs = self._convert_messages(msgs)

            # Claude 不支持原生 JSON mode，在 system prompt 中强调
            if system_prompt:
                system_prompt += "\n\nCRITICAL: You must respond with ONLY valid JSON. No markdown, no explanation."
            else:
                system_prompt = "You must respond with ONLY valid JSON. No markdown, no explanation."

            response = await self._get_client().messages.create(
                model=self.model,
                max_tokens=kwargs.get("max_tokens", self.max_tokens),
                system=system_prompt,
                messages=claude_msgs,
                temperature=kwargs.get("temperature", self.temperature),
            )
            latency_ms = (time.time() - start) * 1000
            if not response.content:
                self._record_call("chat_structured", response.usage, latency_ms,
                                  success=False, error="Claude 返回空", retry_count=attempt)
                msgs.append({"role": "assistant", "content": "[empty response]"})
                msgs.append({
                    "role": "user",
                    "content": "上一轮返回了空响应。请按要求输出 JSON。",
                })
                continue
            raw = response.content[0].text

            # 清理可能的 markdown 包裹
            raw = raw.strip()
            if raw.startswith("```"):
                # 去掉 ```json ... ``` 或 ``` ... ```
                raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._record_call("chat_structured", response.usage, latency_ms,
                                  success=False, error="JSON 解析失败", retry_count=attempt)
                msgs.append({"role": "assistant", "content": raw})
                msgs.append({
                    "role": "user",
                    "content": "上一轮输出不是合法 JSON。请严格只返回纯 JSON。",
                })
                continue

            try:
                result = response_model(**data)
                self._record_call("chat_structured", response.usage, latency_ms,
                                  success=True, retry_count=attempt)
                return result
            except ValidationError as e:
                error_detail = json.dumps(e.errors(), ensure_ascii=False)
                self._record_call("chat_structured", response.usage, latency_ms,
                                  success=False, error=f"校验失败: {error_detail[:200]}",
                                  retry_count=attempt)
                msgs.append({"role": "assistant", "content": raw})
                msgs.append({
                    "role": "user",
                    "content": f"校验失败：{error_detail}\n请按正确格式重新输出。",
                })
                continue

        raise RuntimeError(f"结构化输出失败：重试 {max_retries} 次后仍无法解析。")

    # ----------------------------------------------------------
    # 流式输出
    # ----------------------------------------------------------

    async def chat_stream(
        self,
        messages: list[dict],
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        system_prompt, claude_msgs = self._convert_messages(messages)
        start = time.time()
        try:
            async with self._get_client().messages.stream(
                model=self.model,
                max_tokens=kwargs.get("max_tokens", self.max_tokens),
                system=system_prompt or None,
                messages=claude_msgs,
                temperature=kwargs.get("temperature", self.temperature),
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        finally:
            latency_ms = (time.time() - start) * 1000
            self._record_call("chat_stream", None, latency_ms)
