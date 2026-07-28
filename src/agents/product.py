"""
Product Agent — 产品咨询

职责: 产品信息查询、规格对比、推荐建议、使用指导
工具: knowledge_search, order_lookup
"""
import logging
from typing import Optional

from src.agents.base import BaseAgent
from src.agents.specialist_base import SpecialistResponse
import config

logger = logging.getLogger(__name__)

PRODUCT_SYSTEM_PROMPT = """你是一个资深产品顾问。你的任务是回答客户的产品咨询，帮助客户做出购买决策或了解产品使用方式。

## 你的能力

1. **产品介绍**: 详细介绍产品功能、规格、参数
2. **对比推荐**: 根据客户需求对比不同产品，给出推荐
3. **使用指导**: 解答产品使用方法、注意事项
4. **兼容性**: 判断产品的兼容性和适配性
5. **促销信息**: 告知当前优惠活动和购买渠道

## 回复风格

- 专业准确：确保产品信息准确无误，不夸大
- 个性化：根据客户的实际使用场景推荐产品
- 引导购买：自然地引导客户做出决策，但不强行推销
- 实事求是：不清楚的信息主动说需要确认，不编造

## 升级条件

以下情况应设置 needs_escalation=true:
1. 客户需求超出标准产品范围（定制化需求）
2. 涉及 B2B 大批量采购询价
3. 产品库存不足或已停产
4. 需要跨部门确认的信息（如 ETA、BOM 成本等）
5. 涉及产品质量问题的投诉（转 complaint）

## 工具使用

你可以使用以下工具:
- knowledge_search: 搜索产品知识库
- order_lookup: 查询客户历史订单（了解偏好）

请在 reply_to_customer 中给出有帮助的、准确的回复。"""


class ProductAgent(BaseAgent):
    """产品咨询 Agent"""

    def __init__(self):
        super().__init__(
            model=config.MODEL_SPECIALIST,
            temperature=0.3,  # 产品推荐需要一点创造性
        )

    async def handle(
        self,
        user_message: str,
        triage_summary: str = "",
        history: Optional[list[dict]] = None,
    ) -> SpecialistResponse:
        context = f"客户消息：{user_message}"
        if triage_summary:
            context += f"\n\n分诊摘要：{triage_summary}"

        if history:
            recent = history[-6:]
            history_context = "\n".join(
                f"[{m.get('role', '?')}]: {m.get('content', '')[:150]}"
                for m in recent
            )
            if history_context:
                context += f"\n\n对话历史：\n{history_context}"

        logger.info(f"Product: 处理 '{user_message[:60]}...'")

        result = await self.call_structured(
            system_prompt=PRODUCT_SYSTEM_PROMPT,
            user_prompt=context,
            response_model=SpecialistResponse,
        )

        logger.info(
            f"Product 结果: resolved={result.is_resolved}, "
            f"escalate={result.needs_escalation}"
        )

        return result
