"""
DashScope Provider — 阿里云灵积模型服务

DashScope 提供 OpenAI-compatible API，可直接使用 openai 库调用。
文档: https://help.aliyun.com/document_detail/2712195.html
"""
import json
from typing import AsyncGenerator, Type

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from src.llm.base import LLMProvider
import config


class DashScopeProvider(LLMProvider):
    """阿里云 DashScope LLM Provider"""

    def __init__(self, model: str, temperature: float, max_tokens: int):
        super().__init__(model, temperature, max_tokens)
        self._client: AsyncOpenAI = None  # 懒加载

    def _get_client(self) -> AsyncOpenAI:
        """懒加载 OpenAI-compatible 客户端"""
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=config.DASHSCOPE_API_KEY,
                base_url=config.DASHSCOPE_BASE_URL,
            )
        return self._client

    @property
    def provider_name(self) -> str:
        return "dashscope"

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

        # 注入格式指南到 system prompt
        msgs = list(messages)  # 浅拷贝，避免修改原始 messages
        self._inject_format_guide(msgs, schema)

        for attempt in range(1, max_retries + 1):
            response = await self._get_client().chat.completions.create(
                model=self.model,
                messages=msgs,
                temperature=kwargs.get("temperature", self.temperature),
                max_tokens=kwargs.get("max_tokens", self.max_tokens),
                extra_body={"response_format": {"type": "json_object"}},
            )
            raw = response.choices[0].message.content or ""

            # Step 1: 解析 JSON
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                msgs.append({"role": "assistant", "content": raw})
                msgs.append({
                    "role": "user",
                    "content": "上一轮输出不是合法 JSON。请严格只返回纯 JSON，不要用 ``` 包裹。",
                })
                continue

            # Step 2: Pydantic 校验
            try:
                return response_model(**data)
            except ValidationError as e:
                error_detail = json.dumps(e.errors(), ensure_ascii=False)
                msgs.append({"role": "assistant", "content": raw})
                msgs.append({
                    "role": "user",
                    "content": (
                        f"校验失败，JSON 格式正确但字段不符合要求：\n"
                        f"{error_detail}\n请按照正确格式重新输出。"
                    ),
                })
                continue

        raise RuntimeError(
            f"结构化输出失败：重试 {max_retries} 次后仍然无法解析。"
        )

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

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------

    @staticmethod
    def _inject_format_guide(messages: list[dict], schema: dict) -> None:
        """将 JSON Schema 格式指南注入 system message"""
        fields_desc = []
        for name, prop in schema.get("properties", {}).items():
            ptype = prop.get("type", "string")
            desc = prop.get("description", "")
            if "enum" in prop:
                options = " | ".join(f'"{v}"' for v in prop["enum"])
                fields_desc.append(f'  "{name}": {options}  // {desc}')
            elif ptype == "array":
                items = prop.get("items", {}).get("type", "string")
                fields_desc.append(f'  "{name}": [{"... (string)" if items == "string" else "{...}"}]  // {desc}')
            elif ptype in ("integer", "number"):
                fields_desc.append(f'  "{name}": 0  // {desc}')
            elif ptype == "boolean":
                fields_desc.append(f'  "{name}": true  // {desc}')
            else:
                fields_desc.append(f'  "{name}": "..."  // {desc}')

        format_text = "{\n" + ",\n".join(fields_desc) + "\n}"

        # 找到系统消息并追加格式指南
        for msg in messages:
            if msg.get("role") == "system":
                msg["content"] = (
                    f"{msg['content']}\n\n"
                    f"你必须只返回纯 JSON，格式如下：\n{format_text}\n"
                    f"注意：只返回 JSON 本身，不要包含 ``` 标记或任何解释文字。"
                )
                return

        # 如果没有系统消息，插入一条
        messages.insert(0, {
            "role": "system",
            "content": f"你必须只返回纯 JSON，格式如下：\n{format_text}\n只返回 JSON，不要解释。",
        })
