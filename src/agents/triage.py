"""
Triage Agent — 客服分诊

职责: 意图识别、情感分析、紧急度判断、路由决策

这是客服系统的入口 Agent，决定每个对话应该走向哪个 Specialist。
"""
import logging
from typing import Optional

from pydantic import BaseModel, Field

from src.agents.base import BaseAgent
from src.models.routing import (
    IntentType, Sentiment, Urgency, TriageResult
)
import config

logger = logging.getLogger(__name__)

# ============================================================
# System Prompt
# ============================================================

TRIAGE_SYSTEM_PROMPT = """你是一个资深客服分诊专家。你的任务是分析客户消息的意图、情感和紧急度，并决定路由方向。

## 意图分类

你需要从以下类别中选择最匹配的 primary_intent:

- **faq**: 常见问题，有标准答案（如：如何退货、运费多少、营业时间）
- **technical_support**: 技术问题（如：产品无法开机、App 崩溃、连接失败）
- **billing_account**: 账单或账户问题（如：账单争议、账户余额、订阅管理）
- **product_inquiry**: 产品咨询（如：产品规格、价格、对比、推荐）
- **order_status**: 订单状态查询（如：我的订单到哪了、什么时候发货）
- **refund_request**: 退款请求（如：我要退款、不满意退钱）
- **complaint**: 投诉（如：产品质量差、服务态度不好、物流破损）
- **account_issue**: 账户问题（如：无法登录、密码重置、账户被锁）
- **other**: 以上都不匹配

## 情感分析

分析客户的情感状态:
- **positive**: 满意、感谢、正面
- **neutral**: 中性、客观
- **negative**: 不满、沮丧、失望
- **angry**: 愤怒、投诉、威胁

## 紧急度判断

- **low**: 一般咨询，不紧急
- **medium**: 需要处理但不急迫
- **high**: 影响使用，需要尽快解决
- **critical**: 涉及安全问题、大规模故障、法律风险

## 路由规则

根据意图决定路由:
- faq → faq_answer (可以自己回答，can_self_handle=true)
- technical_support → technical
- billing_account → billing
- product_inquiry → product
- order_status → technical (订单系统技术支持)
- refund_request → billing (退款属账务)
- complaint → complaint (投诉直接给投诉处理)
- account_issue → billing

## 特殊情况

1. 如果客户 anger + urgency=high/critical → 优先路由到 complaint
2. 如果客户明确要求人工 → requires_immediate_human=true
3. 如果涉及人身安全/法律威胁 → requires_immediate_human=true
4. 如果 confidence < 0.6 → 兜底路由到 technical

## 输出要求

你必须输出完整的 JSON，包含所有字段。routing_reason 必须简洁说明为何做此路由决定。"""


# ============================================================
# Agent
# ============================================================

class TriageAgent(BaseAgent):
    """
    分诊 Agent

    输入: 客户消息 + 对话历史
    输出: TriageResult（意图、情感、紧急度、路由建议）
    """

    def __init__(self):
        super().__init__(
            model=config.MODEL_TRIAGE,
            temperature=0.1,  # 分诊需要稳定性
        )

    async def triage(
        self,
        user_message: str,
        history: Optional[list[dict]] = None,
    ) -> TriageResult:
        """
        分析客户消息并做出分诊决策

        Args:
            user_message: 客户最新消息
            history: 对话历史（可选）

        Returns:
            TriageResult
        """
        # 构建增强的用户 prompt
        history_context = ""
        if history:
            recent = history[-6:]  # 最近 3 轮
            history_context = "\n".join(
                f"[{m.get('role', '?')}]: {m.get('content', '')[:200]}"
                for m in recent
            )

        user_prompt = f"客户消息：{user_message}"
        if history_context:
            user_prompt += f"\n\n对话历史（最近几轮）：\n{history_context}"

        logger.info(f"Triage: 分析消息 '{user_message[:60]}...'")

        result = await self.call_structured(
            system_prompt=TRIAGE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=TriageResult,
        )

        logger.info(
            f"Triage 结果: intent={result.primary_intent.value}, "
            f"sentiment={result.sentiment.value}, "
            f"route={result.recommended_agent}, "
            f"confidence={result.intent_confidence:.2f}"
        )

        return result
