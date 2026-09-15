"""
增强 reviews 表 — 新增 handoff_summary / ticket_id 列

Revision ID: 003
Revises: 002
Create Date: 2026-08-27

背景:
  HITL 人工转接升级为真实工单流转，需要:
  - handoff_summary: 转人工摘要（给人工坐席的关键诉求）
  - ticket_id: 转人工/主管裁决自动生成的关联工单号
  开发环境靠 create_all 兜底建表（新表自带），存量库与生产环境需本迁移补列。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    """检查列是否存在（幂等补列）"""
    from sqlalchemy import inspect
    inspector = inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    bind = op.get_bind()

    if not _column_exists(bind, "reviews", "handoff_summary"):
        op.add_column(
            "reviews",
            sa.Column("handoff_summary", sa.Text(), nullable=False, server_default=""),
        )

    if not _column_exists(bind, "reviews", "ticket_id"):
        op.add_column(
            "reviews",
            sa.Column("ticket_id", sa.String(64), nullable=False, server_default=""),
        )
        op.create_index("ix_reviews_ticket_id", "reviews", ["ticket_id"])


def downgrade() -> None:
    bind = op.get_bind()

    if _column_exists(bind, "reviews", "ticket_id"):
        op.drop_index("ix_reviews_ticket_id", table_name="reviews")
        op.drop_column("reviews", "ticket_id")

    if _column_exists(bind, "reviews", "handoff_summary"):
        op.drop_column("reviews", "handoff_summary")
