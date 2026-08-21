"""
Embedding 抽象层 — 文本转向量

通过 DashScope 文本向量模型（text-embedding-v1）经 OpenAI 兼容接口调用。

设计:
- 同步实现（ChromaDB 是同步 API，配合 VectorStore 显式传 embeddings）
- 索引与查询使用同一模型，保证向量空间一致
"""
import logging

import config

logger = logging.getLogger(__name__)

# 单条文本上限（超出截断），避免超过模型 token 上限
MAX_EMBED_CHARS = 1000


class APIEmbedder:
    """通过 DashScope embedding API 将文本转为向量（同步）"""

    def __init__(self, model: str = ""):
        self.model = model or config.EMBEDDING_MODEL

    def embed(self, texts: list[str]) -> list[list[float]]:
        """将文本列表转为向量列表（保持输入顺序）"""
        if not config.DASHSCOPE_API_KEY:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置，无法使用向量嵌入")

        if not texts:
            return []

        import httpx

        trimmed = [t[:MAX_EMBED_CHARS] for t in texts]
        url = config.DASHSCOPE_BASE_URL.rstrip("/") + "/embeddings"
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"},
            json={"model": self.model, "input": trimmed},
            timeout=60,
        )
        if resp.status_code != 200:
            logger.error(f"Embedding API 失败 {resp.status_code}: {resp.text[:200]}")
            raise RuntimeError(f"Embedding API 返回 {resp.status_code}")

        data = resp.json()
        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        vectors = [d["embedding"] for d in items]

        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Embedding 返回数量不匹配: 期望 {len(texts)}，实际 {len(vectors)}"
            )

        return vectors
