"""
知识库层 — ChromaDB 向量存储 + FAQ 管理
"""
from src.knowledge.embedder import Embedder, APIEmbedder, SimpleEmbedder
from src.knowledge.vector_store import VectorStore
from src.knowledge.loader import load_faq_from_json, load_default_faqs
from src.knowledge.parser import parse_file, ParsedDocument
from src.knowledge.chunker import chunk_document, TextChunk
from src.knowledge.ingestion import IngestionPipeline

__all__ = [
    "Embedder", "APIEmbedder", "SimpleEmbedder",
    "VectorStore",
    "load_faq_from_json", "load_default_faqs",
    "parse_file", "ParsedDocument",
    "chunk_document", "TextChunk",
    "IngestionPipeline",
]
