"""
知识库模型 — FAQ 条目和检索结果
"""
from uuid import uuid4
from typing import Optional

from pydantic import BaseModel, Field


class FAQEntry(BaseModel):
    """FAQ 知识库条目"""
    faq_id: str = Field(default_factory=lambda: f"faq_{uuid4().hex[:8]}")
    question: str
    answer: str
    category: str = "general"       # 分类: technical, billing, product, etc.
    tags: list[str] = []            # 标签
    priority: int = 0               # 优先级（越高越常被匹配）
    source: str = ""                # 来源文档/URL


class KnowledgeSearchResult(BaseModel):
    """知识库检索结果"""
    query: str
    results: list[FAQEntry] = []
    top_score: float = 0.0
    search_method: str = "vector"   # vector | keyword | hybrid
    search_time_ms: float = 0.0
