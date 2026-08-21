"""
Context 窗口管理 — 消息条数阈值 + 会话摘要记忆注入

核心策略:
1. 不再用启发式 token 估算（不准确），改用消息条数阈值
2. 超长历史由 graph 的 memory_node 用 LLM 生成会话摘要（记忆），注入各 Agent prompt
3. prepare_context 按 Agent 保留最近 N 条消息，并在开头注入会话摘要
"""
import logging

logger = logging.getLogger(__name__)

# 各 Agent 保留的最近消息条数（更早的旧消息由 memory_node 摘要化）
AGENT_MAX_MESSAGES = {
    "triage":      10,   # 分诊只需最近几轮
    "faq_answer":  8,    # FAQ 极简上下文
    "specialist":  12,   # 专业 Agent 需要更多上下文
    "supervisor":  16,   # 主管需要完整上下文
}


def prepare_context(
    messages: list[dict],
    agent_type: str,
    memory_summary: str = "",
) -> list[dict]:
    """
    为指定 Agent 准备上下文

    Args:
        messages: 原始消息列表
        agent_type: "triage" | "faq_answer" | "specialist" | "supervisor"
        memory_summary: 会话摘要记忆（由 memory_node 生成，覆盖被裁剪的旧消息）

    Returns:
        适合该 Agent 使用的消息列表（[会话记忆] + 最近 N 条消息）
    """
    max_msgs = AGENT_MAX_MESSAGES.get(agent_type, 12)

    result = list(messages)
    if len(result) > max_msgs:
        result = result[-max_msgs:]

    if memory_summary:
        result.insert(0, {
            "role": "system",
            "content": f"[会话记忆] {memory_summary}",
        })

    return result
