"""
客户与订单持久化 — CRM / 订单查询的数据库实现

替代原工具层的硬编码 mock 数据：
- 启动时 seed 演示数据（幂等，仅表空时插入）
- 工具层优先读数据库，数据库不可用/未初始化时回退内置数据（降级）
"""
import json
import logging
from typing import Optional

from sqlalchemy import String, Text, Integer, Float, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.memory.database import Base
from src.models.customer import (
    Customer, CustomerTier, Order, OrderStatus,
)

logger = logging.getLogger(__name__)


# ============================================================
# ORM Model
# ============================================================

class CustomerRow(Base):
    """客户表"""
    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(256), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    tier: Mapped[str] = mapped_column(String(32), default=CustomerTier.STANDARD.value)
    total_orders: Mapped[int] = mapped_column(Integer, default=0)
    total_spent: Mapped[float] = mapped_column(Float, default=0.0)
    joined_at: Mapped[str] = mapped_column(String(64), default="")
    tags_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class OrderRow(Base):
    """订单表"""
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=OrderStatus.PENDING.value)
    items_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(16), default="CNY")
    shipping_address: Mapped[str] = mapped_column(Text, default="")
    tracking_number: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    placed_at: Mapped[str] = mapped_column(String(64), default="")
    shipped_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    delivered_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


# ============================================================
# 演示数据（seed 用，同时作为工具层的兜底数据源）
# ============================================================

DEMO_CUSTOMERS: list[Customer] = [
    Customer(
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
    Customer(
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
    Customer(
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
]

DEMO_ORDERS: list[Order] = [
    Order(
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
    Order(
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
    Order(
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
    Order(
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
]


# ============================================================
# CRUD
# ============================================================

class CustomerStore:
    """客户存储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, customer_id: str) -> Optional[Customer]:
        """按 ID 查询客户"""
        row = await self.db.get(CustomerRow, customer_id)
        if row is None:
            return None
        return self._row_to_model(row)

    async def count(self) -> int:
        """客户总数（用于 seed 幂等判断）"""
        result = await self.db.execute(select(func.count()).select_from(CustomerRow))
        return result.scalar() or 0

    async def add(self, customer: Customer) -> Customer:
        """新增客户（seed 用）"""
        row = CustomerRow(
            customer_id=customer.customer_id,
            name=customer.name,
            email=customer.email,
            phone=customer.phone,
            tier=customer.tier.value,
            total_orders=customer.total_orders,
            total_spent=customer.total_spent,
            joined_at=customer.joined_at,
            tags_json=json.dumps(customer.tags, ensure_ascii=False) if customer.tags else None,
            notes=customer.notes,
        )
        self.db.add(row)
        await self.db.flush()
        return customer

    @staticmethod
    def _row_to_model(row: CustomerRow) -> Customer:
        tags = []
        if row.tags_json:
            try:
                tags = json.loads(row.tags_json)
            except json.JSONDecodeError:
                pass

        return Customer(
            customer_id=row.customer_id,
            name=row.name,
            email=row.email,
            phone=row.phone,
            tier=CustomerTier(row.tier),
            total_orders=row.total_orders,
            total_spent=row.total_spent,
            joined_at=row.joined_at,
            tags=tags,
            notes=row.notes,
        )


class OrderStore:
    """订单存储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, order_id: str) -> Optional[Order]:
        """按 ID 查询订单"""
        row = await self.db.get(OrderRow, order_id)
        if row is None:
            return None
        return self._row_to_model(row)

    async def list_by_customer(self, customer_id: str, limit: int = 50) -> list[Order]:
        """查询某客户的全部订单"""
        stmt = (
            select(OrderRow)
            .where(OrderRow.customer_id == customer_id)
            .order_by(OrderRow.placed_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return [self._row_to_model(row) for row in result.scalars().all()]

    async def count(self) -> int:
        """订单总数（用于 seed 幂等判断）"""
        result = await self.db.execute(select(func.count()).select_from(OrderRow))
        return result.scalar() or 0

    async def add(self, order: Order) -> Order:
        """新增订单（seed 用）"""
        row = OrderRow(
            order_id=order.order_id,
            customer_id=order.customer_id,
            status=order.status.value,
            items_json=json.dumps(order.items, ensure_ascii=False) if order.items else None,
            total_amount=order.total_amount,
            currency=order.currency,
            shipping_address=order.shipping_address,
            tracking_number=order.tracking_number,
            placed_at=order.placed_at,
            shipped_at=order.shipped_at,
            delivered_at=order.delivered_at,
        )
        self.db.add(row)
        await self.db.flush()
        return order

    @staticmethod
    def _row_to_model(row: OrderRow) -> Order:
        items = []
        if row.items_json:
            try:
                items = json.loads(row.items_json)
            except json.JSONDecodeError:
                pass

        return Order(
            order_id=row.order_id,
            customer_id=row.customer_id,
            status=OrderStatus(row.status),
            items=items,
            total_amount=row.total_amount,
            currency=row.currency,
            shipping_address=row.shipping_address,
            tracking_number=row.tracking_number,
            placed_at=row.placed_at,
            shipped_at=row.shipped_at,
            delivered_at=row.delivered_at,
        )


# ============================================================
# Seed
# ============================================================

async def seed_demo_data(db: AsyncSession) -> tuple[int, int]:
    """
    幂等 seed 演示数据：仅在对应表为空时插入。

    Returns:
        (客户数, 订单数) — 本次实际插入的数量
    """
    customer_store = CustomerStore(db)
    order_store = OrderStore(db)

    inserted_customers = 0
    if await customer_store.count() == 0:
        for c in DEMO_CUSTOMERS:
            await customer_store.add(c)
        inserted_customers = len(DEMO_CUSTOMERS)
        logger.info(f"已 seed {inserted_customers} 条演示客户数据")

    inserted_orders = 0
    if await order_store.count() == 0:
        for o in DEMO_ORDERS:
            await order_store.add(o)
        inserted_orders = len(DEMO_ORDERS)
        logger.info(f"已 seed {inserted_orders} 条演示订单数据")

    return inserted_customers, inserted_orders
