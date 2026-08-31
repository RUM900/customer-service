"""
补全 schema — 新增 users / agent_models / faqs / reviews / customers / orders 表

Revision ID: 002
Revises: 001
Create Date: 2026-08-27

背景:
  初始迁移 001 仅建了 sessions/messages/tickets 三张表，其余 6 张表
  在开发环境靠 create_all 兜底建出，生产环境用 alembic upgrade 部署会缺表，
  导致管理员登录、FAQ 管理、模型配置、审核队列、CRM/订单查询全部不可用。
  本迁移补齐这 6 张表，使 alembic upgrade head 与开发态 create_all 产出一致 schema。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, table_name: str) -> bool:
    """检查表是否已存在（幂等建表用）"""
    from sqlalchemy import inspect
    return inspect(bind).has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()

    # --- users 表 ---
    if not _table_exists(bind, "users"):
        op.create_table(
            "users",
            sa.Column("user_id", sa.String(64), primary_key=True),
            sa.Column("username", sa.String(64), nullable=False, unique=True),
            sa.Column("password_hash", sa.String(256), nullable=False),
            sa.Column("role", sa.String(32), nullable=False, server_default="customer"),
            sa.Column("display_name", sa.String(128), server_default=""),
            sa.Column("created_at", sa.String(64), nullable=False),
            sa.Column("last_login_at", sa.String(64), nullable=True),
        )
        op.create_index("ix_users_username", "users", ["username"])

    # --- agent_models 表 ---
    if not _table_exists(bind, "agent_models"):
        op.create_table(
            "agent_models",
            sa.Column("agent_name", sa.String(32), primary_key=True),
            sa.Column("model", sa.String(128), nullable=False),
            sa.Column("updated_at", sa.String(64), nullable=False),
        )

    # --- faqs 表 ---
    if not _table_exists(bind, "faqs"):
        op.create_table(
            "faqs",
            sa.Column("faq_id", sa.String(64), primary_key=True),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("answer", sa.Text(), nullable=False),
            sa.Column("category", sa.String(64), server_default="general"),
            sa.Column("tags_json", sa.Text(), nullable=True),
            sa.Column("priority", sa.Integer(), server_default="0"),
            sa.Column("source", sa.String(256), server_default=""),
            sa.Column("created_at", sa.String(64), nullable=False),
            sa.Column("updated_at", sa.String(64), nullable=False),
        )
        op.create_index("ix_faqs_category", "faqs", ["category"])

    # --- reviews 表 ---
    if not _table_exists(bind, "reviews"):
        op.create_table(
            "reviews",
            sa.Column("review_id", sa.String(64), primary_key=True),
            sa.Column("thread_id", sa.String(128), nullable=False),
            sa.Column("session_id", sa.String(64), server_default=""),
            sa.Column("review_type", sa.String(32), server_default="supervisor_decision"),
            sa.Column("decision_json", sa.Text(), nullable=True),
            sa.Column("review_items_json", sa.Text(), nullable=True),
            sa.Column("status", sa.String(32), server_default="pending"),
            sa.Column("message", sa.Text(), server_default=""),
            sa.Column("reviewer_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.String(64), nullable=False),
            sa.Column("reviewed_at", sa.String(64), nullable=True),
        )
        op.create_index("ix_reviews_thread_id", "reviews", ["thread_id"])
        op.create_index("ix_reviews_session_id", "reviews", ["session_id"])
        op.create_index("ix_reviews_status", "reviews", ["status"])

    # --- customers 表 ---
    if not _table_exists(bind, "customers"):
        op.create_table(
            "customers",
            sa.Column("customer_id", sa.String(64), primary_key=True),
            sa.Column("name", sa.String(128), server_default=""),
            sa.Column("email", sa.String(256), server_default=""),
            sa.Column("phone", sa.String(64), server_default=""),
            sa.Column("tier", sa.String(32), server_default="standard"),
            sa.Column("total_orders", sa.Integer(), server_default="0"),
            sa.Column("total_spent", sa.Float(), server_default="0.0"),
            sa.Column("joined_at", sa.String(64), server_default=""),
            sa.Column("tags_json", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), server_default=""),
        )

    # --- orders 表 ---
    if not _table_exists(bind, "orders"):
        op.create_table(
            "orders",
            sa.Column("order_id", sa.String(64), primary_key=True),
            sa.Column("customer_id", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), server_default="pending"),
            sa.Column("items_json", sa.Text(), nullable=True),
            sa.Column("total_amount", sa.Float(), server_default="0.0"),
            sa.Column("currency", sa.String(16), server_default="CNY"),
            sa.Column("shipping_address", sa.Text(), server_default=""),
            sa.Column("tracking_number", sa.String(128), nullable=True),
            sa.Column("placed_at", sa.String(64), server_default=""),
            sa.Column("shipped_at", sa.String(64), nullable=True),
            sa.Column("delivered_at", sa.String(64), nullable=True),
        )
        op.create_index("ix_orders_customer_id", "orders", ["customer_id"])


def downgrade() -> None:
    op.drop_table("orders")
    op.drop_table("customers")
    op.drop_table("reviews")
    op.drop_table("faqs")
    op.drop_table("agent_models")
    op.drop_table("users")
