"""
文档摄入管道 — 解析 → 分块 → 向量化 → 入库

用法:
    pipe = IngestionPipeline()
    result = await pipe.ingest_file("/path/to/FAQ.pdf")
    # → {"chunks": 15, "indexed": 15, "filename": "FAQ.pdf"}
"""
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from src.knowledge.parser import parse_file, ParsedDocument
from src.knowledge.chunker import chunk_document, TextChunk
from src.knowledge.vector_store import VectorStore

import config

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """
    文档摄入管道

    流程: 上传文件 → 格式解析 → 文本分块 → Embedding → ChromaDB 索引
    """

    def __init__(self, vector_store: Optional[VectorStore] = None):
        self.vector_store = vector_store or VectorStore()
        self._indexed_docs: dict[str, dict] = {}  # filename → {chunks, indexed_at, ...}

    # ----------------------------------------------------------
    # 核心方法
    # ----------------------------------------------------------

    async def ingest_file(
        self,
        file_path: str,
        original_filename: str = "",
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> dict:
        """
        摄入单个文件: 解析 → 分块 → 向量化 → 入库

        Args:
            file_path: 文件在磁盘上的路径
            original_filename: 原始文件名
            chunk_size: 分块大小
            overlap: 块间重叠

        Returns:
            {"chunks": int, "indexed": int, "filename": str, "title": str}
        """
        # Step 1: 解析
        doc = parse_file(file_path, original_filename)
        logger.info(f"摄入: {doc.source} ({doc.format}, {len(doc.content)} 字符)")

        # Step 2: 分块
        chunks = chunk_document(
            content=doc.content,
            title=doc.title,
            source=doc.source,
            chunk_size=chunk_size,
            overlap=overlap,
        )

        # Step 3: 索引到 ChromaDB
        indexed = self._index_chunks(chunks)

        # Step 4: 记录
        from datetime import datetime
        self._indexed_docs[doc.source] = {
            "title": doc.title,
            "format": doc.format,
            "chunks": len(chunks),
            "indexed": indexed,
            "indexed_at": datetime.now().isoformat(),
        }

        logger.info(f"摄入完成: {doc.source} → {indexed}/{len(chunks)} 块已索引")
        return {
            "chunks": len(chunks),
            "indexed": indexed,
            "filename": doc.source,
            "title": doc.title,
            "format": doc.format,
        }

    async def ingest_bytes(
        self,
        file_bytes: bytes,
        filename: str,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> dict:
        """
        从字节流摄入（用于 FastAPI UploadFile）

        Args:
            file_bytes: 文件内容
            filename: 原始文件名
            chunk_size: 分块大小
            overlap: 块间重叠

        Returns:
            {"chunks": int, "indexed": int, "filename": str, "title": str}
        """
        # 写入临时文件
        suffix = Path(filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            result = await self.ingest_file(tmp_path, filename, chunk_size, overlap)
            return result
        finally:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # ----------------------------------------------------------
    # 批量摄入
    # ----------------------------------------------------------

    async def ingest_directory(self, dir_path: str) -> list[dict]:
        """摄入整个目录下的所有支持文件"""
        results = []
        path = Path(dir_path)
        for f in path.iterdir():
            if f.is_file():
                try:
                    result = await self.ingest_file(str(f), f.name)
                    results.append(result)
                except ValueError as e:
                    logger.warning(f"跳过 {f.name}: {e}")
                except Exception as e:
                    logger.error(f"摄入失败 {f.name}: {e}")
                    results.append({"filename": f.name, "error": str(e)})
        return results

    # ----------------------------------------------------------
    # 管理
    # ----------------------------------------------------------

    def list_documents(self) -> list[dict]:
        """列出所有已索引的文档"""
        return [
            {"filename": k, **v}
            for k, v in self._indexed_docs.items()
        ]

    def get_document(self, filename: str) -> Optional[dict]:
        """获取某个文档的索引信息"""
        return self._indexed_docs.get(filename)

    def remove_document(self, filename: str) -> bool:
        """
        从索引中移除文档的所有块

        直接从 ChromaDB 按 source 元数据删除（不依赖进程内 _indexed_docs，
        这样重启后管理员仍可删除已上传文档）。
        """
        removed_from_index = filename in self._indexed_docs
        if removed_from_index:
            del self._indexed_docs[filename]

        # 从 ChromaDB 中删除该文档的块
        removed_chunks = False
        try:
            if self.vector_store and self.vector_store._collection:
                existing = self.vector_store._collection.get(
                    where={"source": filename}
                )
                if existing and existing["ids"]:
                    ids_to_delete = existing["ids"]
                    # 分批删除（ChromaDB 单次删除可能有限制）
                    batch_size = 500
                    for i in range(0, len(ids_to_delete), batch_size):
                        batch = ids_to_delete[i:i + batch_size]
                        self.vector_store._collection.delete(ids=batch)
                    logger.info(f"已从索引移除: {filename} ({len(ids_to_delete)} 个块)")
                    removed_chunks = True
        except Exception as e:
            logger.warning(f"ChromaDB 删除失败: {e}")

        return removed_from_index or removed_chunks

    @property
    def document_count(self) -> int:
        return len(self._indexed_docs)

    @property
    def total_chunks(self) -> int:
        return sum(d["chunks"] for d in self._indexed_docs.values())

    # ----------------------------------------------------------
    # 内部
    # ----------------------------------------------------------

    def _index_chunks(self, chunks: list[TextChunk]) -> int:
        """将分块索引到 ChromaDB"""
        if not self.vector_store:
            return 0
        # 懒初始化 ChromaDB
        if not self.vector_store._collection and not self.vector_store._init_chroma():
            logger.warning("ChromaDB 未就绪，跳过向量索引（分块可通过关键词检索）")
            return 0

        ids = []
        documents = []
        metadatas = []

        for chunk in chunks:
            ids.append(chunk.chunk_id)
            documents.append(chunk.text)
            metadatas.append({
                "title": chunk.title,
                "source": chunk.source,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text[:1000],  # 元数据存前 1000 字符
                "kind": "document",
            })

        self.vector_store.index(ids=ids, documents=documents, metadatas=metadatas)
        return len(ids)
