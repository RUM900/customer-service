"""
Technical Agent — 技术支持

职责: 技术问题诊断、排查步骤指导、Bug 报告、技术知识库检索
工具: knowledge_search, ticket_create
"""
import logging
from typing import Optional

from src.agents.base import BaseAgent
from src.agents.specialist_base import SpecialistResponse, build_specialist_context
import config

logger = logging.getLogger(__name__)

# ============================================================
# System Prompt
# ============================================================

TECHNICAL_SYSTEM_PROMPT = """你是一个资深技术支持专家。你的任务是诊断客户的技术问题，以及处理订单状态查询。

## 你的能力

1. **问题诊断**: 通过客户描述快速定位根本原因
2. **排查指导**: 给出清晰的、分步骤的排查操作
3. **解决方案**: 提供明确的修复方案或 workaround
4. **知识库检索**: 利用 FAQ 知识库查找已知问题和解决方案
5. **工单管理**: 对复杂问题创建工单进行跟踪

## 回复风格

- 专业但不晦涩：用客户能理解的语言解释技术问题
- 结构化：使用分步骤、分要点的方式组织回复
- 共情：理解客户遇到技术问题时的困扰
- 主动：给出预防类似问题的建议

## 升级条件

以下情况应设置 needs_escalation=true:
1. 涉及系统级 Bug，需要开发团队修复
2. 问题原因不明确，经过排查仍无法定位
3. 涉及数据安全/隐私问题
4. 客户使用了不支持的配置/环境
5. 需要物理维修/更换硬件

## 工具使用

你可以使用以下工具（在 tools_to_use 字段中列出）:
- **knowledge_search**: 搜索 FAQ/知识库（技术问题、故障排查）
- **order_lookup**: 查询订单详情（订单状态、物流、商品信息）
- **order_status**: 快速查询订单状态（仅状态+物流，比 order_lookup 轻量）
- **ticket_create**: 创建技术支持工单（复杂问题需要跟踪时使用）

**重要**: 如果客户询问订单相关问题，优先使用 order_lookup 或 order_status，而不是 knowledge_search。

请在 reply_to_customer 中直接给出对客户有帮助的回复。"""


class TechnicalAgent(BaseAgent):
    """技术支持 Agent"""

    def __init__(self):
        from src.api.model_registry import get_model

        super().__init__(
            model=get_model("technical"),
            temperature=0.2,
        )

    async def handle(
        self,
        user_message: str,
        triage_summary: str = "",
        history: Optional[list[dict]] = None,
    ) -> SpecialistResponse:
        """
        处理技术支持请求

        Args:
            user_message: 客户消息
            triage_summary: Triage Agent 的分析摘要
            history: 对话历史

        Returns:
            SpecialistResponse
        """
        context = build_specialist_context(user_message, triage_summary, history)

        logger.info(f"Technical: 处理 '{user_message[:60]}...'")

        result = await self.call_structured(
            system_prompt=TECHNICAL_SYSTEM_PROMPT,
            user_prompt=context,
            response_model=SpecialistResponse,
        )

        logger.info(
            f"Technical 结果: resolved={result.is_resolved}, "
            f"escalate={result.needs_escalation}, "
            f"confidence={result.confidence:.2f}"
        )

        return result
