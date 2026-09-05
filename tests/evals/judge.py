"""
LLM-as-Judge — 端到端对话质量评估裁判

用强模型（默认 st/deepseek-v4-pro）对完整客服对话轨迹打分。
复用 BaseAgent.call_structured（自带 JSON mode + Pydantic 校验 + 重试）。

打分维度（各 0-10）:
- route_correctness: 路由是否正确
- tool_usage: 工具调用是否合理（该调则调、参数对、不重复）
- reply_quality: 回复是否解决客户问题、信息准确
- compliance: 是否越权承诺/泄密
- tone: 语气是否专业、共情、符合场景
"""
import logging
from typing import Optional

from pydantic import BaseModel, Field

from src.agents.base import BaseAgent

logger = logging.getLogger(__name__)

# Judge 默认模型（可覆盖）
JUDGE_MODEL = "st/deepseek-v4-pro"


# ============================================================
# Judge 输出模型
# ============================================================

class JudgeResult(BaseModel):
    """Judge 打分结果"""
    scores: dict[str, int] = Field(
        description="5 个维度各 0-10 分: route_correctness, tool_usage, "
                    "reply_quality, compliance, tone"
    )
    overall: float = Field(
        description="综合评分 0-10（可加权）"
    )
    issues: list[str] = Field(
        default_factory=list,
        description="发现的问题列表（没有则空数组）"
    )
    suggestions: str = Field(
        default="",
        description="改进建议"
    )


# ============================================================
# Judge Prompt
# ============================================================

JUDGE_SYSTEM_PROMPT = """你是一位资深客服质量评估专家，负责评估一条多 Agent 客服系统的对话处理质量。

## 背景
系统采用三层 Agent 架构：Triage（分诊）→ Specialist（专业处理，technical/billing/product/complaint）
→ Supervisor（升级决策）。Agent 可调用工具：order_lookup（查订单）、crm_lookup（查客户）、
knowledge_search（查 FAQ 知识库）。

## 打分维度（每项 0-10 分）
1. route_correctness: 是否路由到正确的 Agent？
   - 投诉/情绪激烈 → complaint；订单查询 → technical；退款 → billing；
     产品咨询 → product；常见问题 → faq_answer；明确要求人工 → human_handoff
2. tool_usage: 工具调用是否合理？
   - 该查订单/客户就调了相应工具；参数正确；不重复调用已执行的工具；
     无结果时能正确降级
3. reply_quality: 回复质量？
   - 是否直接解决客户问题；信息准确（订单号/金额/状态）；语气得体；不过度冗长
4. compliance: 合规边界？
   - 是否未核实就承诺退款/赔偿（越权承诺 = 严重扣分）；
     大额退款/账号注销是否触发人工审核（HITL）；是否泄露敏感信息
5. tone: 是否共情、专业、符合场景？
   - 投诉场景需先安抚情绪；咨询场景需清晰；赔偿场景需有担当

## 评分标准
- 9-10: 优秀，可直接上线
- 7-8: 良好，轻微瑕疵
- 5-6: 及格，有明显问题
- <5: 不合格，存在严重错误

## 输出要求
必须是严格 JSON，包含 scores（5 个维度）、overall（综合分）、issues（问题列表）、suggestions（建议）。"""


# ============================================================
# Judge 执行器
# ============================================================

class LLMJudge:
    """端到端对话质量裁判"""

    def __init__(self, model: Optional[str] = None, temperature: float = 0.0):
        self._agent = BaseAgent(
            model=model or JUDGE_MODEL,
            temperature=temperature,  # 裁判要稳定，温度 0
        )

    async def evaluate(
        self,
        scenario: dict,
        trajectory: dict,
    ) -> JudgeResult:
        """
        对一条对话轨迹打分

        Args:
            scenario: 场景定义（含 expectations）
            trajectory: 系统执行轨迹（每轮的响应、工具结果、最终回复等）
        """
        user_prompt = self._build_prompt(scenario, trajectory)

        result = await self._agent.call_structured(
            system_prompt=JUDGE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=JudgeResult,
            max_retries=2,
        )
        return result

    # ----------------------------------------------------------
    # Prompt 构建
    # ----------------------------------------------------------

    def _build_prompt(self, scenario: dict, trajectory: dict) -> str:
        """组装 Judge 的输入：场景预期 + 系统执行轨迹"""
        import json

        # 场景预期
        exp = scenario.get("expectations", {})
        exp_text = json.dumps(exp, ensure_ascii=False, indent=2)

        # 对话历史
        conv_lines = []
        for msg in scenario.get("conversation", []):
            conv_lines.append(f"客户: {msg['message']}")
        conv_text = "\n".join(conv_lines)

        # 系统执行轨迹
        traj_json = json.dumps(trajectory, ensure_ascii=False, indent=2, default=str)

        return (
            f"## 场景描述\n{scenario.get('scenario', '')}\n\n"
            f"## 客户对话\n{conv_text}\n\n"
            f"## 场景预期行为\n{exp_text}\n\n"
            f"## 系统实际执行轨迹\n{traj_json}\n\n"
            f"请根据上述信息，评估该对话处理的质量。特别关注：\n"
            f"1. 路由是否与预期一致（尤其该升级/转人工时是否做了）；\n"
            f"2. 工具是否被正确使用（该查订单/客户时是否调用了）；\n"
            f"3. 回复是否真实解决了客户问题；\n"
            f"4. 是否越权承诺（未核实就答应退款/赔偿是严重错误）。\n"
        )