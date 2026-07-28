"""
Context 窗口管理 — Token 估算 + 滑动窗口截断 + 摘要压缩

核心策略:
1. 每个 Agent 有独立的 context 预算（system_prompt + history + user_msg + output）
2. 优先保留最近消息 + 用户消息 + system_prompt
3. 超限时从最早的消息开始截断
4. 极端情况生成摘要替代旧消息
"""
import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)

# ============================================================
# Token 估算（无需 tiktoken 依赖）
# ============================================================


def estimate_tokens(text: str) -> int:
    """
    估算文本的 token 数

    规则:
    - 中文字符 ≈ 1.5 tokens/字
    - 英文单词 ≈ 1.3 tokens/词
    - 数字/符号 ≈ 1 token/个
    - 总体用 字符数/2.5 作为粗略估算（cl100k_base 的分词率）
    """
    if not text:
        return 0

    # 分别统计中文和非中文字符
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
    other_chars = len(text) - chinese_chars

    # 中文: ~1.5 tokens/字, 其他: ~0.3 tokens/字符 (英文按词分)
    return int(chinese_chars * 1.5 + other_chars * 0.4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """估算消息列表的总 token 数"""
    total = 0
    for m in messages:
        content = m.get("content", "") if isinstance(m, dict) else str(m)
        total += estimate_tokens(str(content))
        # 每条消息有 ~4 tokens 的格式开销（role, 分隔符等）
        total += 4
    return total


# ============================================================
# 消息截断
# ============================================================


def truncate_messages(
    messages: list[dict],
    max_tokens: int,
    system_prompt: str = "",
    user_message: str = "",
    reserve_output: int = 1024,
) -> list[dict]:
    """
    截断消息列表，确保总 token 数不超过预算

    优先级: system_prompt > user_message > 最近消息 > 最早消息

    Args:
        messages: 对话历史 [{"role": ..., "content": ...}]
        max_tokens: 总 token 预算
        system_prompt: 系统提示（不计入 messages 但占用预算）
        user_message: 当前用户消息（计入预算）
        reserve_output: 为 LLM 输出预留的 token 数

    Returns:
        截断后的消息列表
    """
    # 计算固定开销
    fixed_tokens = (
        estimate_tokens(system_prompt)
        + estimate_tokens(user_message)
        + reserve_output
    )

    available = max_tokens - fixed_tokens
    if available <= 0:
        logger.warning(
            f"Token 预算不足: max={max_tokens}, fixed={fixed_tokens}, "
            f"请减少 system_prompt 或增大 max_tokens"
        )
        # 紧急：只保留最近 2 条
        return messages[-2:] if len(messages) > 2 else messages

    # 从最新到最旧累积，直到接近上限
    result = []
    used = 0
    for m in reversed(messages):
        content = m.get("content", "") if isinstance(m, dict) else str(m)
        msg_tokens = estimate_tokens(str(content)) + 4  # 4 tokens 格式开销

        if used + msg_tokens > available:
            break

        result.insert(0, m)  # 保持时间顺序（旧→新）
        used += msg_tokens

    trimmed = len(messages) - len(result)
    if trimmed > 0:
        logger.info(
            f"消息截断: {len(messages)} → {len(result)} "
            f"({trimmed} 条旧消息被移除, 使用 {used}/{available} tokens)"
        )

    return result


# ============================================================
# 摘要压缩（超长对话时使用）
# ============================================================


def compress_history(
    messages: list[dict],
    max_tokens: int,
    system_prompt: str = "",
    user_message: str = "",
) -> list[dict]:
    """
    压缩对话历史

    策略:
    1. 如果消息能放入预算 → 直接返回
    2. 否则截断旧消息
    3. 如果截断后仍有 3+ 条被移除 → 在开头插入一条摘要占位

    Args:
        messages: 完整对话历史
        max_tokens: 总 token 预算
        system_prompt: 系统提示
        user_message: 当前用户消息

    Returns:
        压缩后的消息列表
    """
    if not messages:
        return []

    full_tokens = estimate_messages_tokens(messages) + estimate_tokens(system_prompt) + estimate_tokens(user_message)

    if full_tokens <= max_tokens * 0.85:  # 85% 以内，不需要压缩
        return messages

    truncated = truncate_messages(
        messages, max_tokens,
        system_prompt=system_prompt,
        user_message=user_message,
    )

    removed = len(messages) - len(truncated)
    if removed >= 3:
        # 插入摘要占位：提示 Agent 有旧消息被省略
        summary = {
            "role": "system",
            "content": (
                f"[上下文提示] 以上省略了 {removed} 条较早的对话消息。"
                f"如果客户引用了之前的内容，请注意这可能来自被省略的部分。"
            ),
        }
        truncated.insert(0, summary)
        logger.info(f"插入摘要占位: {removed} 条旧消息被压缩")

    return truncated


# ============================================================
# Agent 专用截断
# ============================================================

# 各 Agent 的 context 预算（token 数）
# 预留 system_prompt + 输出空间后的实际可用量
AGENT_CONTEXT_BUDGETS = {
    "triage":      3500,   # Triage: 短 system prompt, 小模型
    "faq_answer":  3000,   # FAQ: 极简 prompt
    "specialist":  4000,   # Specialist: 长 system prompt, 需要领域知识
    "supervisor":  5000,   # Supervisor: 最强模型, 需要完整上下文
}


def prepare_context(
    messages: list[dict],
    agent_type: str,
    system_prompt: str = "",
    user_message: str = "",
) -> list[dict]:
    """
    为指定 Agent 准备上下文

    自动根据 Agent 类型选择合适的截断策略。

    Args:
        messages: 原始消息列表
        agent_type: "triage" | "faq_answer" | "specialist" | "supervisor"
        system_prompt: Agent 的 system prompt
        user_message: 当前用户消息

    Returns:
        适合该 Agent 使用的消息列表
    """
    budget = AGENT_CONTEXT_BUDGETS.get(agent_type, 4000)
    return compress_history(messages, budget, system_prompt, user_message)
