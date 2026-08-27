"""
OpenAI Provider — 支持 OpenAI 官方 API 及兼容服务

支持:
- OpenAI 官方 (api.openai.com)
- Azure OpenAI
- 其他 OpenAI-compatible 服务（vLLM, LocalAI 等）
"""
import json
import time
from typing import AsyncGenerator, Type

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from src.llm.base import LLMProvider
from src.llm.format_utils import inject_format_guide, extract_json
from src.llm.retry import llm_retry
import config


class OpenAIProvider(LLMProvider):
    """OpenAI / OpenAI-compatible Provider"""

    def __init__(self, model: str, temperature: float, max_tokens: int):
        super().__init__(model, temperature, max_tokens)
        self._client: AsyncOpenAI = None  # 懒加载

    def _get_client(self) -> AsyncOpenAI:
        """懒加载 OpenAI 客户端"""
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=config.OPENAI_API_KEY,
                base_url=config.OPENAI_BASE_URL,
            )
        return self._client

    @property
    def provider_name(self) -> str:
        return "openai"

    # ----------------------------------------------------------
    # 普通对话
    # ----------------------------------------------------------

    @llm_retry
    async def chat(self, messages: list[dict], **kwargs) -> str:
        start = time.time()
        response = await self._get_client().chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
        )
        latency_ms = (time.time() - start) * 1000
        self._record_call("chat", response.usage, latency_ms)
        return response.choices[0].message.content or ""

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

        msgs = list(messages)
        inject_format_guide(msgs, schema)

        for attempt in range(1, max_retries + 1):
            start = time.time()
            try:
                # OpenAI 原生支持 response_format
                response = await self._get_client().chat.completions.create(
                    model=self.model,
                    messages=msgs,
                    temperature=kwargs.get("temperature", self.temperature),
                    max_tokens=kwargs.get("max_tokens", self.max_tokens),
                    response_format={"type": "json_object"},
                )
            except Exception:
                # 降级：不强制 json_object
                response = await self._get_client().chat.completions.create(
                    model=self.model,
                    messages=msgs,
                    temperature=kwargs.get("temperature", self.temperature),
                    max_tokens=kwargs.get("max_tokens", self.max_tokens),
                )
            latency_ms = (time.time() - start) * 1000
            raw = response.choices[0].message.content or ""

            try:
                data = json.loads(extract_json(raw))
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
        start = time.time()
        try:
            stream = await self._get_client().chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", self.temperature),
                max_tokens=kwargs.get("max_tokens", self.max_tokens),
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        finally:
            latency_ms = (time.time() - start) * 1000
            self._record_call("chat_stream", None, latency_ms)

