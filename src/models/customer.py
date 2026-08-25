"""
客户模型 — 客户信息、工单、订单
"""
from datetime import datetime
from enum import Enum
from uuid import uuid4
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# 客户
# ============================================================

class CustomerTier(str, Enum):
    """客户等级"""
    STANDARD = "standard"
    PREMIUM = "premium"
    VIP = "vip"


class Customer(BaseModel):
    """客户信息"""
    customer_id: str = Field(default_factory=lambda: f"cust_{uuid4().hex[:8]}")
    name: str = ""
    email: str = ""
    phone: str = ""
    tier: CustomerTier = CustomerTier.STANDARD
    total_orders: int = 0
    total_spent: float = 0.0
    joined_at: str = ""
    tags: list[str] = []
    notes: str = ""


# ============================================================
# 订单
# ============================================================

class OrderStatus(str, Enum):
    """订单状态"""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    RETURNED = "returned"


class Order(BaseModel):
    """订单信息"""
    order_id: str = Field(default_factory=lambda: f"ord_{uuid4().hex[:8]}")
    customer_id: str = ""
    status: OrderStatus = OrderStatus.PENDING
    items: list[dict] = []              # [{name, quantity, price}]
    total_amount: float = 0.0
    currency: str = "CNY"
    shipping_address: str = ""
    tracking_number: Optional[str] = None
    placed_at: str = ""
    shipped_at: Optional[str] = None
    delivered_at: Optional[str] = None


# ============================================================
# 工单
# ============================================================

class TicketPriority(str, Enum):
    """工单优先级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TicketStatus(str, Enum):
    """工单状态"""
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_CUSTOMER = "waiting_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Ticket(BaseModel):
    """客服工单"""
    ticket_id: str = Field(default_factory=lambda: f"ticket_{uuid4().hex[:8]}")
    customer_id: str = ""
    session_id: str = ""
    subject: str = ""
    description: str = ""
    priority: TicketPriority = TicketPriority.MEDIUM
    status: TicketStatus = TicketStatus.OPEN
    assigned_agent: Optional[str] = None
    resolution: Optional[str] = None
    tags: list[str] = []
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    closed_at: Optional[str] = None
