"""
Embedding 抽象层 — 文本转向量

支持:
- API 模式: 调用 LLM 提供的 embedding API（如 text-embedding-v3）
- 本地模式: 使用 sentence-transformers（可选，适合离线/低成本场景）
"""
import logging
from abc import ABC, abstractmethod
from typing import Optional

import config

logger = logging.getLogger(__name__)


class Embedder(ABC):
    """Embedding 抽象基类"""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """将文本列表转为向量列表"""
        ...

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """将单条查询文本转为向量"""
        ...


class APIEmbedder(Embedder):
    """
    通过 LLM API 获取 embedding

    使用 DashScope / OpenAI 的 embedding API。
    """

    def __init__(self, model: Optional[str] = None):
        self.model = model or config.EMBEDDING_MODEL

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # 使用 openai 库调用 embedding API
        from openai import AsyncOpenAI
        import config as cfg

        client = AsyncOpenAI(
            api_key=cfg.DASHSCOPE_API_KEY,
            base_url=cfg.DASHSCOPE_BASE_URL,
        )

        response = await client.embeddings.create(
            model=self.model,
            input=texts,
        )
        return [d.embedding for d in response.data]

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]


class SimpleEmbedder(Embedder):
    """
    简易 Embedder — 基于关键词的伪向量

    用于没有 embedding API 时的 fallback。
    将文本转为基于词频的稀疏向量表示。

    注意: 这不是真正的语义搜索，只是一个降级方案。
    """

    def __init__(self, vector_size: int = 256):
        self.vector_size = vector_size
        self._vocab: dict[str, int] = {}
        self._next_id = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._text_to_vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._text_to_vector(text)

    def _text_to_vector(self, text: str) -> list[float]:
        """将文本转为一个简易的稀疏向量"""
        import hashlib

        vec = [0.0] * self.vector_size

        # 分词
        words = text.lower().split()
        for word in words:
            # 用 hash 确定位置
            h = int(hashlib.md5(word.encode()).hexdigest()[:8], 16)
            idx = h % self.vector_size
            vec[idx] += 1.0

        # L2 归一化
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]

        return vec
