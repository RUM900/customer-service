"""
CRM 工具 — 客户信息查询（模拟数据）

真实环境应对接实际的 CRM 系统（如 Salesforce、Zendesk）。
"""
import logging

from src.tools.base import BaseTool
from src.models.customer import Customer, CustomerTier

logger = logging.getLogger(__name__)

# ============================================================
# 模拟客户数据
# ============================================================

_MOCK_CUSTOMERS = {
    "cust_001": Customer(
        customer_id="cust_001",
        name="张三",
        email="zhangsan@example.com",
        phone="13800000001",
        tier=CustomerTier.VIP,
        total_orders=156,
        total_spent=28900.50,
        joined_at="2023-01-15",
        tags=["vip", "长期客户", "高价值"],
    ),
    "cust_002": Customer(
        customer_id="cust_002",
        name="李四",
        email="lisi@example.com",
        phone="13800000002",
        tier=CustomerTier.PREMIUM,
        total_orders=42,
        total_spent=8750.00,
        joined_at="2024-03-20",
        tags=["premium", "活跃"],
    ),
    "cust_003": Customer(
        customer_id="cust_003",
        name="王五",
        email="wangwu@example.com",
        phone="13800000003",
        tier=CustomerTier.STANDARD,
        total_orders=3,
        total_spent=450.00,
        joined_at="2025-06-01",
        tags=["新客户"],
    ),
}


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
        customer = _MOCK_CUSTOMERS.get(customer_id)
        if customer is None:
            return {
                "found": False,
                "customer_id": customer_id,
                "message": f"未找到客户 {customer_id}",
            }

        logger.info(f"CRM 查询: {customer_id} → {customer.name}")
        return {
            "found": True,
            "customer": customer.model_dump(),
        }
