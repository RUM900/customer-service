"""
CRM 工具 — 客户信息查询

数据源策略: 数据库优先，内置演示数据兜底（降级）
- 正常: 从 customers 表读取（启动时 seed 演示数据）
- 数据库不可用/未初始化: 回退到内置演示数据，保证服务可用

真实环境可将 execute() 内的查询替换为外部 CRM API 调用（如 Salesforce）。
"""
import logging

from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class CRMLookupTool(BaseTool):
    """客户信息查询工具"""

    name = "crm_lookup"
    description = (
        "查询客户信息：根据客户 ID 获取客户资料，包括姓名、等级、"
        "历史订单数、总消费金额、标签等。"
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "客户 ID，如 cust_001",
                },
            },
            "required": ["customer_id"],
        }

    async def execute(self, customer_id: str) -> dict:
        # --- 优先读数据库 ---
        try:
            from src.memory.database import get_session_factory
            from src.memory.customer_store import CustomerStore

            factory = get_session_factory()
            async with factory() as db:
                customer = await CustomerStore(db).get(customer_id)

            if customer is not None:
                logger.info(f"CRM 查询(DB): {customer_id} → {customer.name}")
                return {"found": True, "customer": customer.model_dump()}
        except Exception as e:
            logger.warning(f"CRM 数据库查询失败，回退内置数据: {e}")

        # --- 回退内置演示数据（数据库不可用/未初始化） ---
        from src.memory.customer_store import DEMO_CUSTOMERS

        customer = next((c for c in DEMO_CUSTOMERS if c.customer_id == customer_id), None)
        if customer is None:
            return {
                "found": False,
                "customer_id": customer_id,
                "message": f"未找到客户 {customer_id}",
            }

        logger.info(f"CRM 查询(内置): {customer_id} → {customer.name}")
        return {
            "found": True,
            "customer": customer.model_dump(),
        }
