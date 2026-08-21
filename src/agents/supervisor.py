"""
Supervisor Agent — 监管/升级处理

职责: 接收升级案例、跨域协调、最终决策、人工转接判定
工具: 所有工具（ALL）
"""
import logging
from typing import Optional

from src.agents.base import BaseAgent
from src.agents.specialist_base import SupervisorDecision
import config

logger = logging.getLogger(__name__)

SUPERVISOR_SYSTEM_PROMPT = """你是一个高级客服主管。你接收从 Specialist Agent 升级上来的案例，做出最终决策。

## 你的职责

1. **升级审核**: 审查 Specialist Agent 的升级原因和处理过程
2. **终局决策**: 做出 final 级别的决定（退款金额、补偿方案、是否转人工）
3. **跨域协调**: 当问题涉及多个领域时，协调不同 Agent 共同解决
4. **人工转接**: 判断是否必须转人工处理

## 决策指南

### 直接解决（action=resolve）
- Specialist 的诊断正确，只是权限不够 → 你直接批准解决方案
- 可以给出比 Specialist 更优的补偿方案
- 问题已有明确答案，只是需要主管确认

### 升级转人工（action=escalate_to_human）
- 涉及法律/合规风险
- 客户明确要求且情绪无法平复
- 涉及人身安全/产品安全
- 需要线下操作（如上门维修、实物检测）

### 跨域协调（action=coordinate）
- 问题涉及多个领域（如：技术问题+账单退款）
- 需要其他 Specialist 的补充意见

### 拒绝升级（action=reject）
- Specialist 判断有误，其实自身可以解决
- 升级理由不充分

## 回复风格

- 权威但不傲慢：作为主管，决策应该坚定但礼貌
- 清晰：明确告知客户最终结果和后续步骤
- 有担当：对公司的决定负责，不推脱

## 人工审核标记（require_human_review）

以下情况必须设置 require_human_review=true，并在 review_items 中列出具体审核项：
- 退款金额 >= 500 元
- 涉及账户注销/数据删除
- 涉及法律/合规风险
- 补偿总价值 >= 300 元
- 你自己对决策置信度较低（< 0.7）

设置 require_human_review 后，系统暂停执行，等待人工审核。

## 工具使用

你有权限使用所有工具:
- crm_lookup, order_lookup, order_status
- knowledge_search
- ticket_create, ticket_query
- human_handoff

请在做出决策后给出完整的 SupervisorDecision。"""


class SupervisorAgent(BaseAgent):
    """监管 Agent"""

    def __init__(self):
        from src.api.model_registry import get_model

        super().__init__(
            model=get_model("supervisor"),
            temperature=0.15,
        )

    async def decide(
        self,
        escalation_context: str,
        specialist_result: dict,
        history: Optional[list[dict]] = None,
    ) -> SupervisorDecision:
        """
        做出监管决策

        Args:
            escalation_context: 升级上下文（升级原因、已尝试方案等）
            specialist_result: Specialist Agent 的输出
            history: 完整对话历史

        Returns:
            SupervisorDecision
        """
        user_prompt = (
            f"## 升级上下文\n{escalation_context}\n\n"
            f"## Specialist 处理结果\n{specialist_result}"
        )

        if history:
            recent = history[-8:]
            history_context = "\n".join(
                f"[{m.get('role', '?')}]: {m.get('content', '')[:200]}"
                for m in recent
            )
            user_prompt += f"\n\n## 对话历史\n{history_context}"

        logger.info(f"Supervisor: 审查升级案例...")

        result = await self.call_structured(
            system_prompt=SUPERVISOR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=SupervisorDecision,
        )

        logger.info(
            f"Supervisor 决策: action={result.action}, "
            f"handoff={result.handoff_required}"
        )

        return result
