"""
订单工具 — 订单查询

数据源策略: 数据库优先，内置演示数据兜底（降级）
- 正常: 从 orders 表读取（启动时 seed 演示数据）
- 数据库不可用/未初始化: 回退到内置演示数据，保证服务可用

真实环境可将 execute() 内的查询替换为订单系统 API 调用。
"""
import logging

from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class OrderLookupTool(BaseTool):
    """订单查询工具"""

    name = "order_lookup"
    description = (
        "查询订单信息：提供 order_id 查询单个订单，或提供 customer_id 查询该客户全部订单。"
        "客户查询自己的订单时用 customer_id（无需客户提供订单号）。"
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
        # --- 优先读数据库 ---
        try:
            from src.memory.database import get_session_factory
            from src.memory.customer_store import OrderStore

            factory = get_session_factory()
            async with factory() as db:
                store = OrderStore(db)

                if order_id:
                    order = await store.get(order_id)
                    if order is not None:
                        logger.info(f"订单查询(DB): {order_id} → {order.status.value}")
                        return {"found": True, "order": order.model_dump()}

                if customer_id:
                    orders = await store.list_by_customer(customer_id)
                    logger.info(f"客户订单查询(DB): {customer_id} → {len(orders)} 条")
                    return {
                        "found": len(orders) > 0,
                        "customer_id": customer_id,
                        "orders": [o.model_dump() for o in orders],
                        "total": len(orders),
                    }
        except Exception as e:
            logger.warning(f"订单数据库查询失败，回退内置数据: {e}")

        # --- 回退内置演示数据（数据库不可用/未初始化） ---
        from src.memory.customer_store import DEMO_ORDERS

        if order_id:
            order = next((o for o in DEMO_ORDERS if o.order_id == order_id), None)
            if order is None:
                return {"found": False, "order_id": order_id, "message": f"未找到订单 {order_id}"}
            logger.info(f"订单查询(内置): {order_id} → {order.status.value}")
            return {"found": True, "order": order.model_dump()}

        if customer_id:
            orders = [o for o in DEMO_ORDERS if o.customer_id == customer_id]
            logger.info(f"客户订单查询(内置): {customer_id} → {len(orders)} 条")
            return {
                "found": len(orders) > 0,
                "customer_id": customer_id,
                "orders": [o.model_dump() for o in orders],
                "total": len(orders),
            }

        return {"found": False, "message": "请提供 order_id 或 customer_id"}
