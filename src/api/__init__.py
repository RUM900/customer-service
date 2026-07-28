"""
FastAPI 层 — REST API + SSE Streaming
"""
from src.api.routes import router
from src.api.middleware import setup_middleware
from src.api.deps import get_db, get_graph, get_tool_registry, get_knowledge_store

__all__ = [
    "router",
    "setup_middleware",
    "get_db",
    "get_graph",
    "get_tool_registry",
    "get_knowledge_store",
]
