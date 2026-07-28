"""
向量存储 — ChromaDB 封装

提供 FAQ 知识库的向量存储和检索。
支持:
- 索引 FAQ 条目
- 语义搜索
- 过滤（按分类/标签）
"""
import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)


class VectorStore:
    """
    ChromaDB 向量存储封装

    用于 FAQ 知识库的持久化和检索。
    """

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        collection_name: Optional[str] = None,
    ):
        persist_dir = persist_dir or config.CHROMA_PERSIST_DIR
        collection_name = collection_name or config.CHROMA_COLLECTION_NAME

        import os
        os.makedirs(persist_dir, exist_ok=True)

        # 完全懒加载 — 构造时不触碰 ChromaDB
        self._ready = False
        self._collection = None
        self._client = None
        self._persist_dir = persist_dir
        self._collection_name = collection_name

    def _init_chroma(self) -> bool:
        """
        显式初始化 ChromaDB（仅在需要向量检索时调用，懒加载依赖）
        返回 True 表示初始化成功
        """
        if self._ready:
            return True
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
            self._client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
            )
            self._ready = True
            logger.info(
                f"ChromaDB 已连接: {self._persist_dir}, "
                f"集合: {self._collection_name}, "
                f"文档数: {self._collection.count()}"
            )
            return True
        except Exception as e:
            logger.warning(f"ChromaDB 初始化失败，使用关键词检索: {e}")
            self._client = None
            self._collection = None
            self._ready = False
            return False

    # ----------------------------------------------------------
    # 索引
    # ----------------------------------------------------------

    def index(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
        embeddings: Optional[list[list[float]]] = None,
    ) -> None:
        """
        索引文档到 ChromaDB

        Args:
            ids: 文档 ID 列表
            documents: 文档文本列表
            metadatas: 元数据列表
            embeddings: 可选的预计算向量
        """
        if not self._collection:
            logger.warning("ChromaDB 未就绪，跳过索引")
            return
        self._collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        logger.info(f"已索引 {len(ids)} 条文档")

    def index_faqs(self, faq_entries: list[dict]) -> None:
        """
        索引 FAQ 条目

        Args:
            faq_entries: [{"faq_id": ..., "question": ..., "answer": ..., "category": ..., "tags": [...]}]
        """
        # 清空已有数据
        try:
            ids_to_delete = self._collection.get()["ids"]
            if ids_to_delete:
                self._collection.delete(ids=ids_to_delete)
        except Exception:
            pass

        ids = []
        documents = []
        metadatas = []

        for entry in faq_entries:
            faq_id = entry.get("faq_id", f"faq_{len(ids)}")
            ids.append(faq_id)
            # 将 question + answer 拼接为文档
            documents.append(
                f"Q: {entry.get('question', '')}\nA: {entry.get('answer', '')}"
            )
            metadatas.append({
                "question": entry.get("question", ""),
                "answer": entry.get("answer", ""),
                "category": entry.get("category", "general"),
                "tags": ",".join(entry.get("tags", [])),
            })

        if ids:
            self.index(ids=ids, documents=documents, metadatas=metadatas)
            logger.info(f"FAQ 索引完成: {len(ids)} 条")

    # ----------------------------------------------------------
    # 检索
    # ----------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 5,
        category: Optional[str] = None,
    ) -> list[dict]:
        """
        语义搜索

        Args:
            query: 查询文本
            top_k: 返回结果数
            category: 可选的分类过滤

        Returns:
            [{"faq_id": ..., "question": ..., "answer": ..., "score": ..., "category": ...}]
        """
        if not self._collection:
            return []  # ChromaDB 未就绪，返回空

        where_filter = None
        if category:
            where_filter = {"category": category}

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where_filter,
            )
        except Exception as e:
            logger.error(f"ChromaDB 查询失败: {e}")
            return []

        formatted = []
        if results and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results.get("distances") else 0
                # ChromaDB distance → similarity score
                score = 1.0 / (1.0 + distance) if distance else 0.9 - (i * 0.1)

                metadata = results["metadatas"][0][i] if results.get("metadatas") else {}
                document = results["documents"][0][i] if results.get("documents") else ""

                formatted.append({
                    "faq_id": doc_id,
                    "question": metadata.get("question", ""),
                    "answer": metadata.get("answer", document),
                    "score": round(max(0.1, min(score, 1.0)), 3),
                    "category": metadata.get("category", ""),
                    "tags": (metadata.get("tags", "") or "").split(","),
                })

        return formatted

    # ----------------------------------------------------------
    # 管理
    # ----------------------------------------------------------

    def count(self) -> int:
        """文档总数"""
        return self._collection.count() if self._collection else 0

    def clear(self) -> None:
        """清空集合"""
        if not self._collection:
            return
        ids = self._collection.get()["ids"]
        if ids:
            self._collection.delete(ids=ids)
        logger.info("集合已清空")
