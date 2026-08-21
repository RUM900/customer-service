"""
订单工具 — 订单查询与操作（模拟数据）

真实环境应对接实际的订单系统（如电商平台的订单 API）。
"""
import logging

from src.tools.base import BaseTool
from src.models.customer import Order, OrderStatus

logger = logging.getLogger(__name__)

# ============================================================
# 模拟订单数据
# ============================================================

_MOCK_ORDERS = {
    "ord_001": Order(
        order_id="ord_001",
        customer_id="cust_001",
        status=OrderStatus.SHIPPED,
        items=[
            {"name": "机械键盘 K8 Pro", "quantity": 1, "price": 599.00},
            {"name": "鼠标垫 XL", "quantity": 2, "price": 49.00},
        ],
        total_amount=697.00,
        currency="CNY",
        shipping_address="北京市朝阳区xxx路100号",
        tracking_number="SF1234567890",
        placed_at="2025-07-01T10:30:00",
        shipped_at="2025-07-02T14:00:00",
    ),
    "ord_002": Order(
        order_id="ord_002",
        customer_id="cust_001",
        status=OrderStatus.PROCESSING,
        items=[
            {"name": "显示器 27寸 4K", "quantity": 1, "price": 2999.00},
        ],
        total_amount=2999.00,
        currency="CNY",
        shipping_address="北京市朝阳区xxx路100号",
        tracking_number=None,
        placed_at="2025-07-05T09:00:00",
    ),
    "ord_003": Order(
        order_id="ord_003",
        customer_id="cust_002",
        status=OrderStatus.DELIVERED,
        items=[
            {"name": "蓝牙耳机 Pro", "quantity": 1, "price": 899.00},
        ],
        total_amount=899.00,
        currency="CNY",
        shipping_address="上海市浦东新区xxx路200号",
        tracking_number="YT9876543210",
        placed_at="2025-06-28T16:00:00",
        shipped_at="2025-06-29T10:00:00",
        delivered_at="2025-07-01T08:30:00",
    ),
    "ord_004": Order(
        order_id="ord_004",
        customer_id="cust_003",
        status=OrderStatus.PROCESSING,
        items=[
            {"name": "手机壳 iPhone 15", "quantity": 1, "price": 99.00},
        ],
        total_amount=99.00,
        currency="CNY",
        shipping_address="广州市天河区xxx路50号",
        tracking_number="ZTO1122334455",
        placed_at="2025-06-25T12:00:00",
    ),
}


class OrderLookupTool(BaseTool):
    """订单查询工具"""

    name = "order_lookup"
    description = (
        "查询订单信息：根据订单 ID 或客户 ID 获取订单详情。"
        "返回订单状态、商品列表、金额、物流信息等。"
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "订单 ID，如 ord_001（与 customer_id 二选一）",
                },
                "customer_id": {
                    "type": "string",
                    "description": "客户 ID，如 cust_001（查询该客户的所有订单）",
                },
            },
        }

    async def execute(
        self,
        order_id: str = "",
        customer_id: str = "",
    ) -> dict:
        # 按订单 ID 查询
        if order_id:
            order = _MOCK_ORDERS.get(order_id)
            if order is None:
                return {"found": False, "order_id": order_id, "message": f"未找到订单 {order_id}"}
            logger.info(f"订单查询: {order_id} → {order.status.value}")
            return {"found": True, "order": order.model_dump()}

        # 按客户 ID 查询
        if customer_id:
            orders = [
                o for o in _MOCK_ORDERS.values()
                if o.customer_id == customer_id
            ]
            logger.info(f"客户订单查询: {customer_id} → {len(orders)} 条")
            return {
                "found": len(orders) > 0,
                "customer_id": customer_id,
                "orders": [o.model_dump() for o in orders],
                "total": len(orders),
            }

        return {"found": False, "message": "请提供 order_id 或 customer_id"}
