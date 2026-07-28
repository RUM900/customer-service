"""
OpenAI Provider — 支持 OpenAI 官方 API 及兼容服务

支持:
- OpenAI 官方 (api.openai.com)
- Azure OpenAI
- 其他 OpenAI-compatible 服务（vLLM, LocalAI 等）
"""
import json
from typing import AsyncGenerator, Type

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from src.llm.base import LLMProvider
from src.llm.format_utils import inject_format_guide
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

    async def chat(self, messages: list[dict], **kwargs) -> str:
        response = await self._get_client().chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
        )
        return response.choices[0].message.content or ""

    # ----------------------------------------------------------
    # 结构化输出
    # ----------------------------------------------------------

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

            raw = response.choices[0].message.content or ""

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                msgs.append({"role": "assistant", "content": raw})
                msgs.append({
                    "role": "user",
                    "content": "上一轮输出不是合法 JSON。请严格只返回纯 JSON。",
                })
                continue

            try:
                return response_model(**data)
            except ValidationError as e:
                error_detail = json.dumps(e.errors(), ensure_ascii=False)
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

