"""
Complaint Agent — 投诉处理

职责: 客户投诉接收、共情沟通、问题调查、补偿方案
工具: crm_lookup, order_lookup, ticket_create, human_handoff
"""
import logging
from typing import Optional

from src.agents.base import BaseAgent
from src.agents.specialist_base import SpecialistResponse, build_specialist_context
import config

logger = logging.getLogger(__name__)

COMPLAINT_SYSTEM_PROMPT = """你是一个资深投诉处理专家。你的任务是处理客户的投诉，安抚客户情绪，调查问题原因，并提供合理的解决方案。

## 你的能力

1. **情绪安抚**: 首先共情客户，让客户感受到被重视
2. **问题调查**: 通过 CRM 和订单系统了解客户背景和问题详情
3. **责任划分**: 判断问题的责任方
4. **补偿方案**: 根据客户等级和问题严重程度提出补偿（退款、优惠券、积分、赠品等）
5. **流程改进**: 记录投诉原因，反馈给相关部门

## 回复风格

- **共情第一**: 先道歉、共情，再进入解决问题
- 不推诿: 即使是客户的责任，也以帮助的态度解决
- 实质方案: 给出具体的补偿数额和时间承诺
- 专业克制: 即使面对不合理要求也保持专业

## 补偿梯度（根据客户等级和问题严重度）

| 客户等级 | 轻微问题 | 一般问题 | 严重问题 |
|---------|---------|---------|---------|
| VIP     | 50元券   | 200元券  | 退款+300元券 |
| Premium | 30元券   | 100元券  | 退款+150元券 |
| Standard| 20元券   | 50元券   | 退款+50元券  |

## 升级条件

以下情况应设置 needs_escalation=true:
1. 客户要求退款金额超出补偿梯度
2. 涉及人身伤害/安全的产品问题
3. 可能引起法律纠纷的投诉
4. 客户威胁在社交媒体曝光
5. 涉及第三方（如物流公司）的责任纠纷

## 工具使用

你可以使用以下工具:
- crm_lookup: 查询客户等级和历史
- order_lookup: 查询相关订单
- ticket_create: 创建投诉工单
- human_handoff: 升级到人工处理

**工具参数**: 使用工具时，请在 tool_calls 字段给出参数 args，如 [{'tool': 'order_lookup', 'args': {'order_id': 'ord_001'}}]。订单号/客户 ID 从对话历史中提取；无法确定时可省略 args。

请在 reply_to_customer 中给出温暖的、有实质内容的回复。"""


class ComplaintAgent(BaseAgent):
    """投诉处理 Agent"""

    def __init__(self):
        from src.api.model_registry import get_model

        super().__init__(
            model=get_model("complaint"),
            temperature=0.3,  # 投诉处理需要一定的情感智能
        )

    async def handle(
        self,
        user_message: str,
        triage_summary: str = "",
        history: Optional[list[dict]] = None,
    ) -> SpecialistResponse:
        context = build_specialist_context(
            user_message, triage_summary, history,
            triage_prefix="分诊摘要（注意情感状态）",
        )

        logger.info(f"Complaint: 处理 '{user_message[:60]}...'")

        result = await self.call_structured(
            system_prompt=COMPLAINT_SYSTEM_PROMPT,
            user_prompt=context,
            response_model=SpecialistResponse,
        )

        logger.info(
            f"Complaint 结果: resolved={result.is_resolved}, "
            f"escalate={result.needs_escalation}"
        )

        return result
