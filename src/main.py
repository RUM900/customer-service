"""
FastAPI 入口 — Multi-Tier Customer Service System

启动:
    uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

访问:
    Swagger UI: http://localhost:8000/docs
    ReDoc:      http://localhost:8000/redoc
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from src.api.routes import router
from src.api.middleware import setup_middleware
from src.api.deps import get_knowledge_store

import config

# ============================================================
# 日志配置
# ============================================================

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format=config.LOG_FORMAT,
)
logger = logging.getLogger(__name__)


# ============================================================
# 应用生命周期
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭时的生命周期管理"""
    # === 启动 ===
    logger.info("=" * 60)
    logger.info("客服系统启动中...")
    logger.info(f"LLM Provider: {config.LLM_PROVIDER}")
    logger.info(f"API: http://{config.API_HOST}:{config.API_PORT}")
    logger.info(f"Swagger: http://{config.API_HOST}:{config.API_PORT}/docs")
    logger.info("=" * 60)

    yield

    # === 关闭 ===
    logger.info("客服系统正在关闭...")

    # 关闭数据库连接
    try:
        from src.memory.database import close_db
        await close_db()
    except Exception:
        pass

    logger.info("客服系统已关闭")


# ============================================================
# 创建 App
# ============================================================

app = FastAPI(
    title="Multi-Tier Customer Service System",
    description="""
## 多层级智能客服协同系统

基于多 Agent 协作的智能客服系统，支持：
- **三层 Agent 架构**: Triage → Specialist → Supervisor
- **自动意图识别**: 智能路由到正确的专业 Agent
- **情感感知**: 根据客户情感调整回复策略
- **升级管理**: 复杂问题自动升级到主管或人工
- **知识库检索**: ChromaDB 向量检索 FAQ 知识库
- **流式响应**: SSE 实时推送 Agent 处理进度
    """,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# --- 注册中间件 ---
setup_middleware(app)

# --- 注册路由 ---
app.include_router(router)
from src.api.admin_routes import router as admin_router
app.include_router(admin_router)


# ============================================================
# 聊天界面
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def chat_ui():
    """内置 Web 聊天界面"""
    from pathlib import Path
    html_path = Path(__file__).parent / "static" / "chat.html"
    return html_path.read_text(encoding="utf-8")


# ============================================================
# 直接运行
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host=config.API_HOST,
        port=config.API_PORT,
        reload=True,
        log_level=config.LOG_LEVEL.lower(),
    )
