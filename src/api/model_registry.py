"""
Agent 模型注册中心 — DB 持久化 + 内存缓存 + 运行时热切换

设计:
- 启动时从 DB 加载全部 Agent 模型配置到内存缓存
- Agent 在 __init__ 时同步读取缓存（回落 config 默认值）
- 管理员修改后写 DB + 更新缓存 + 重置 Agent 单例，下次请求立即生效
"""
import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)

# 内存缓存: agent_name → model
_cache: dict[str, str] = {}


# ============================================================
# 默认模型（config 回落值）
# ============================================================

def _default_model(agent_name: str) -> str:
    """config 中的默认模型"""
    if agent_name == "triage":
        return config.MODEL_TRIAGE
    if agent_name == "supervisor":
        return config.MODEL_SUPERVISOR
    # technical / billing / product / complaint 共用 specialist 槽位
    return config.MODEL_SPECIALIST


# ============================================================
# 读取
# ============================================================

def get_model(agent_name: str) -> str:
    """同步获取某 Agent 的模型（DB 缓存优先，回落 config 默认）"""
    return _cache.get(agent_name) or _default_model(agent_name)


def get_all_models() -> dict[str, str]:
    """返回全部 Agent 的当前模型配置（含 config 默认回落）"""
    from src.memory.model_config import AGENT_SLOTS

    result = {}
    for slot in AGENT_SLOTS:
        result[slot] = get_model(slot)
    return result


# ============================================================
# 加载 / 更新
# ============================================================

async def load_from_db() -> None:
    """启动时从 DB 加载全部模型配置到缓存"""
    from src.memory.database import get_session_factory
    from src.memory.model_config import ModelConfigStore

    try:
        factory = get_session_factory()
        async with factory() as db:
            store = ModelConfigStore(db)
            rows = await store.get_all()
            _cache.update(rows)
            logger.info(f"模型配置已从 DB 加载: {rows or '(无自定义，使用默认)'}")
    except Exception as e:
        logger.warning(f"模型配置加载失败，使用默认值: {e}")


async def update_model(agent_name: str, model: str) -> None:
    """更新某 Agent 的模型：写 DB → 更新缓存 → 重置 Agent 单例"""
    from src.memory.database import get_session_factory
    from src.memory.model_config import ModelConfigStore

    async with get_session_factory()() as db:
        try:
            store = ModelConfigStore(db)
            await store.upsert(agent_name, model)
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    _cache[agent_name] = model

    # 重置 Agent 单例缓存，使新模型在下次请求生效
    from src.graph.workflow import reset_agents
    reset_agents()
    logger.info(f"模型配置已更新: {agent_name} → {model}")


# ============================================================
# 模型名校验（可选探活）
# ============================================================

async def validate_model(model: str) -> Optional[str]:
    """
    校验模型名是否可用（MODEL_VALIDATION_ENABLED=true 时调用）

    用 max_tokens=1 的 chat 探活作为权威校验（DeepSeek 等提供商的
    models.list 可能只返回当前主推版本，历史别名仍可用，不能据此拒绝）。
    无 API Key 或校验关闭时跳过，返回 None（表示无需处理）。
    """
    if not config.MODEL_VALIDATION_ENABLED:
        return None
    if not (config.OPENAI_API_KEY or config.DASHSCOPE_API_KEY or config.ANTHROPIC_API_KEY):
        return None

    try:
        from openai import AsyncOpenAI

        api_key = config.OPENAI_API_KEY or config.DASHSCOPE_API_KEY or config.ANTHROPIC_API_KEY
        base_url = config.OPENAI_BASE_URL or config.DASHSCOPE_BASE_URL

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)

        try:
            await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return None
        except Exception as e:
            return f"模型 '{model}' 校验失败: {e}"
    except Exception as e:
        return f"模型校验器不可用: {e}"
