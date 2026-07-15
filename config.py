"""
配置中心 — 所有可调参数集中管理

支持多 LLM Provider、PostgreSQL、ChromaDB 等各层配置。
环境变量优先级：.env > 系统环境变量 > 默认值
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env（从项目根目录）
load_dotenv(Path(__file__).parent / ".env")

# ============================================================
# LLM Provider 配置
# ============================================================

# 当前使用的 LLM Provider: "dashscope" | "openai" | "claude"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "dashscope")

# Mock 模式 — 设为 "mock" 可离线验证完整链路
# MOCK_RESPONSES=true 时 LLM 调用返回预设回复，不消耗 API 额度

# --- DashScope (阿里云) ---
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)

# --- OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# --- Claude (Anthropic) ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")

# ============================================================
# 模型分层 — 不同 Agent 用不同能力的模型
# ============================================================

# Triage Agent: 需要快速响应，小模型即可
MODEL_TRIAGE = os.getenv("MODEL_TRIAGE", "qwen-turbo")

# Specialist Agents: 需要领域知识和推理能力
MODEL_SPECIALIST = os.getenv("MODEL_SPECIALIST", "qwen-turbo")

# Supervisor Agent: 复杂决策，需要最强模型
MODEL_SUPERVISOR = os.getenv("MODEL_SUPERVISOR", "qwen-plus")

# FAQ Embedding 模型
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")

# ============================================================
# LLM 参数
# ============================================================

DEFAULT_TEMPERATURE = float(os.getenv("DEFAULT_TEMPERATURE", "0.1"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4096"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_MIN_WAIT = int(os.getenv("RETRY_MIN_WAIT", "2"))
RETRY_MAX_WAIT = int(os.getenv("RETRY_MAX_WAIT", "30"))

# ============================================================
# PostgreSQL 配置
# ============================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/customer_service",
)
DATABASE_POOL_SIZE = int(os.getenv("DATABASE_POOL_SIZE", "10"))
DATABASE_POOL_OVERFLOW = int(os.getenv("DATABASE_POOL_OVERFLOW", "20"))

# ============================================================
# ChromaDB 配置
# ============================================================

CHROMA_PERSIST_DIR = os.getenv(
    "CHROMA_PERSIST_DIR",
    str(Path(__file__).parent / "data" / "chroma"),
)
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "faq_knowledge")

# ============================================================
# 客服业务配置
# ============================================================

# 最大升级轮次（防止无限升级循环）
MAX_ESCALATION_ROUNDS = int(os.getenv("MAX_ESCALATION_ROUNDS", "2"))

# FAQ 直接回复的置信度阈值
FAQ_CONFIDENCE_THRESHOLD = float(os.getenv("FAQ_CONFIDENCE_THRESHOLD", "0.75"))

# 对话历史保留轮数
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "20"))

# 会话超时（秒）
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "1800"))

# Context 窗口管理
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "4000"))
# 各 Agent 的 token 预算（可单独覆盖）
CONTEXT_BUDGET_TRIAGE = int(os.getenv("CONTEXT_BUDGET_TRIAGE", "3500"))
CONTEXT_BUDGET_SPECIALIST = int(os.getenv("CONTEXT_BUDGET_SPECIALIST", "4000"))
CONTEXT_BUDGET_SUPERVISOR = int(os.getenv("CONTEXT_BUDGET_SUPERVISOR", "5000"))

# ============================================================
# API 配置
# ============================================================

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
API_WORKERS = int(os.getenv("API_WORKERS", "4"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))

# ============================================================
# 日志配置
# ============================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = os.getenv(
    "LOG_FORMAT",
    "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
)
