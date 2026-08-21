"""
Billing Agent — 账单/账户

职责: 账单查询、退款处理、账户管理、支付问题
工具: crm_lookup, order_lookup, ticket_create
"""
import logging
from typing import Optional

from src.agents.base import BaseAgent
from src.agents.specialist_base import SpecialistResponse, build_specialist_context
import config

logger = logging.getLogger(__name__)

BILLING_SYSTEM_PROMPT = """你是一个资深账单与账户客服专家。你的任务是处理客户关于账单、支付、退款和账户的问题。

## 你的能力

1. **账单查询**: 查询和解释账单明细、费用构成
2. **支付问题**: 处理支付失败、重复扣款、支付方式变更
3. **退款处理**: 评估退款资格、计算退款金额、发起退款流程
4. **账户管理**: 账户余额查询、充值、订阅管理、账户变更
5. **订单关联**: 查询订单对应的账单和支付状态

## 回复风格

- 准确透明：明确列出每一笔费用，不模糊其辞
- 合规严谨：退款/支付操作必须说明政策依据
- 耐心细致：账单问题客户往往比较焦虑，需要耐心解释
- 主动建议：为客户提供省钱建议（如年度订阅 vs 月度订阅）

## 升级条件

以下情况应设置 needs_escalation=true:
1. 退款金额超过你的权限范围（如 >500 元）
2. 涉及法律/合规风险的账单争议
3. 客户要求关闭账户并永久删除数据
4. 涉嫌欺诈的交易
5. 多次退款请求异常的客户

## 工具使用

你可以使用以下工具:
- crm_lookup: 查询客户信息和等级
- order_lookup: 查询订单详情和支付状态
- ticket_create: 创建工单跟踪

请在 reply_to_customer 中给出完整且准确的回复。"""


class BillingAgent(BaseAgent):
    """账单/账户 Agent"""

    def __init__(self):
        from src.api.model_registry import get_model

        super().__init__(
            model=get_model("billing"),
            temperature=0.1,  # 账单需要精确
        )

    async def handle(
        self,
        user_message: str,
        triage_summary: str = "",
        history: Optional[list[dict]] = None,
    ) -> SpecialistResponse:
        context = build_specialist_context(user_message, triage_summary, history)

        logger.info(f"Billing: 处理 '{user_message[:60]}...'")

        result = await self.call_structured(
            system_prompt=BILLING_SYSTEM_PROMPT,
            user_prompt=context,
            response_model=SpecialistResponse,
        )

        logger.info(
            f"Billing 结果: resolved={result.is_resolved}, "
            f"escalate={result.needs_escalation}"
        )

        return result
