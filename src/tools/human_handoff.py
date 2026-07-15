"""
人工转接工具 — 将对话转接给人工客服

触发条件:
- 客户明确要求转人工
- Agent 无法解决问题（超过能力范围）
- Supervisor 判断需要人工介入
- 敏感/紧急问题（如投诉升级、法律风险）
"""
import logging

from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class HumanHandoffTool(BaseTool):
    """人工转接工具"""

    name = "human_handoff"
    description = (
        "将当前对话转接给人工客服。"
        "适用场景：Agent 无法解决、客户明确要求、紧急/敏感问题、"
        "涉及退款/赔偿等需要人工审批的操作。"
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "当前会话 ID",
                },
                "reason": {
                    "type": "string",
                    "description": "转接原因（如：'客户要求退款，超出自动处理范围'）",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "转接优先级",
                },
                "summary": {
                    "type": "string",
                    "description": "给人工客服的问题摘要（包含已尝试的解决方案）",
                },
            },
            "required": ["session_id", "reason", "summary"],
        }

    async def execute(
        self,
        session_id: str,
        reason: str,
        summary: str,
        priority: str = "medium",
    ) -> dict:
        logger.warning(f"人工转接: session={session_id}, reason={reason}")

        # 真实环境：写入转接队列、通知人工坐席等
        return {
            "handoff_requested": True,
            "session_id": session_id,
            "reason": reason,
            "priority": priority,
            "summary": summary,
            "estimated_wait_minutes": 5 if priority != "critical" else 1,
            "message": (
                f"已为您转接人工客服，预计等待 {5 if priority != 'critical' else 1} 分钟。"
                f"转接原因：{reason}"
            ),
        }
