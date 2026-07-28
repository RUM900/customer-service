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
import config

logger = logging.getLogger(__name__)


class KnowledgeSearchTool(BaseTool):
    """
    FAQ 知识库检索工具

    通过向量相似度检索最匹配的 FAQ 条目。
    如果 ChromaDB 未初始化或无数据，降级为关键词匹配。
    """

    name = "knowledge_search"
    description = (
        "搜索 FAQ 知识库，获取与客户问题最匹配的答案。"
        "适用于常见问题、产品信息、政策说明等。"
    )

    def __init__(self):
        self._collection = None
        self._faq_data: list[dict] = []  # fallback 数据

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

        # 尝试向量检索
        results = self._vector_search(query, category, top_k)

        # fallback: 关键词匹配
        if not results:
            results = self._keyword_search(query, category, top_k)

        elapsed_ms = (time.time() - start) * 1000

        return {
            "results": results,
            "top_score": results[0]["score"] if results else 0.0,
            "method": "vector" if self._collection else "keyword",
            "search_time_ms": round(elapsed_ms, 2),
        }

    # ----------------------------------------------------------
    # 向量检索
    # ----------------------------------------------------------

    def _vector_search(
        self, query: str, category: Optional[str], top_k: int
    ) -> list[dict]:
        """ChromaDB 向量检索"""
        if not self._collection:
            return []

        try:
            where_filter = None
            if category:
                where_filter = {"category": category}

            results = self._collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where_filter,
            )

            formatted = []
            if results and results["ids"] and results["ids"][0]:
                for i, doc_id in enumerate(results["ids"][0]):
                    formatted.append({
                        "faq_id": doc_id,
                        "question": results["metadatas"][0][i].get("question", ""),
                        "answer": results["documents"][0][i],
                        "score": 1.0 - (i * 0.15),  # 近似分数
                        "category": results["metadatas"][0][i].get("category", ""),
                    })
            return formatted

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
        """加载 FAQ 数据到内存（fallback）"""
        self._faq_data = faq_entries
        logger.info(f"FAQ 数据已加载: {len(faq_entries)} 条")

    def init_chroma(self, persist_dir: Optional[str] = None) -> None:
        """初始化 ChromaDB 集合（懒加载 chromadb 依赖）"""
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
            persist_dir = persist_dir or config.CHROMA_PERSIST_DIR
            client = chromadb.PersistentClient(
                path=persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )

            self._collection = client.get_or_create_collection(
                name=config.CHROMA_COLLECTION_NAME,
            )
            logger.info(f"ChromaDB 已初始化: {persist_dir}, 集合: {config.CHROMA_COLLECTION_NAME}")
        except Exception as e:
            logger.warning(f"ChromaDB 初始化失败，将使用关键词匹配: {e}")
            self._collection = None

    def index_faqs(self, faq_entries: list[dict], embed_func=None) -> None:
        """
        将 FAQ 数据索引到 ChromaDB

        Args:
            faq_entries: FAQ 条目列表
            embed_func: 可选的 embedding 函数
        """
        if not self._collection:
            logger.warning("ChromaDB 未初始化，跳过索引")
            return

        # 清空已有数据
        try:
            self._collection.delete(where={})
        except Exception:
            pass

        # 批量添加
        ids = []
        documents = []
        metadatas = []

        for entry in faq_entries:
            faq_id = entry.get("faq_id", f"faq_{len(ids)}")
            ids.append(faq_id)
            documents.append(entry.get("answer", ""))
            metadatas.append({
                "question": entry.get("question", ""),
                "category": entry.get("category", "general"),
                "tags": ",".join(entry.get("tags", [])),
            })

        if ids:
            self._collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )
            logger.info(f"已索引 {len(ids)} 条 FAQ 到 ChromaDB")

        # 同时更新内存数据
        self._faq_data = faq_entries
