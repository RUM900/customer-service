"""
知识库检索工具 — 基于 ChromaDB 的 FAQ 向量检索

提供:
- 语义搜索 FAQ 知识库
- 关键词搜索（fallback）
- 混合检索（向量 + 关键词）
"""
import logging
import time
from typing import Optional

from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class KnowledgeSearchTool(BaseTool):
    """
    FAQ 知识库检索工具

    向量检索委托给共享的 VectorStore 单例（与知识库管理端同一个实例，
    避免多个 ChromaDB 客户端操作同一路径）。
    如果向量库未就绪或无结果，降级为关键词匹配。
    """

    name = "knowledge_search"
    description = (
        "搜索 FAQ 知识库，获取与客户问题最匹配的答案。"
        "适用于常见问题、产品信息、政策说明等。"
    )

    def __init__(self):
        self._vector_store = None  # 共享 VectorStore 单例（懒加载）
        self._faq_data: list[dict] = []  # 关键词 fallback 数据

    # ----------------------------------------------------------
    # 参数 Schema
    # ----------------------------------------------------------

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询，用自然语言描述客户问题",
                },
                "category": {
                    "type": "string",
                    "description": "可选：限定搜索分类。technical | billing | product | general",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回结果数量，默认 3",
                },
            },
            "required": ["query"],
        }

    # ----------------------------------------------------------
    # 执行
    # ----------------------------------------------------------

    async def execute(self, query: str, category: Optional[str] = None, top_k: int = 3) -> dict:
        """
        搜索知识库

        Args:
            query: 搜索查询
            category: 可选分类过滤
            top_k: 返回结果数

        Returns:
            {"results": [...], "top_score": float, "method": str}
        """
        start = time.time()

        # 优先向量检索，无结果再降级关键词
        results = self._vector_search(query, category, top_k)
        method = "vector"
        if not results:
            results = self._keyword_search(query, category, top_k)
            method = "keyword"

        elapsed_ms = (time.time() - start) * 1000

        return {
            "results": results,
            "top_score": results[0]["score"] if results else 0.0,
            "method": method,
            "search_time_ms": round(elapsed_ms, 2),
        }

    # ----------------------------------------------------------
    # 向量检索
    # ----------------------------------------------------------

    def _get_vector_store(self):
        """懒加载共享的 VectorStore（与知识库管理端同一个实例）"""
        if self._vector_store is None:
            from src.api.deps import get_knowledge_store
            self._vector_store = get_knowledge_store()
        return self._vector_store

    def _vector_search(
        self, query: str, category: Optional[str], top_k: int
    ) -> list[dict]:
        """ChromaDB 向量检索（委托给共享 VectorStore）"""
        vs = self._get_vector_store()
        if vs is None or not vs._collection:
            return []

        try:
            results = vs.search(query, top_k=top_k, category=category)
            return [
                {
                    "faq_id": r["faq_id"],
                    "question": r["question"],
                    "answer": r["answer"],
                    "score": r["score"],
                    "category": r.get("category", ""),
                }
                for r in results
            ]
        except Exception as e:
            logger.warning(f"向量检索失败: {e}")
            return []

    # ----------------------------------------------------------
    # 关键词检索（fallback）
    # ----------------------------------------------------------

    def _keyword_search(
        self, query: str, category: Optional[str], top_k: int
    ) -> list[dict]:
        """关键词匹配降级方案"""
        if not self._faq_data:
            return []

        query_lower = query.lower()
        scored = []

        for entry in self._faq_data:
            if category and entry.get("category") != category:
                continue

            # 简易 TF 评分
            question = entry.get("question", "").lower()
            answer = entry.get("answer", "").lower()
            keywords = query_lower.split()

            score = 0
            for kw in keywords:
                score += question.count(kw) * 3  # 标题匹配权重高
                score += answer.count(kw)

            if score > 0:
                scored.append({**entry, "score": min(score / 10, 0.95)})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    # ----------------------------------------------------------
    # 数据加载
    # ----------------------------------------------------------

    def load_data(self, faq_entries: list[dict]) -> None:
        """加载 FAQ 数据到内存（关键词 fallback）"""
        self._faq_data = faq_entries
        logger.info(f"FAQ 数据已加载: {len(faq_entries)} 条")
