"""
新增 copilot_logs 表 — 坐席 Copilot 调用审计（采纳率/成本/质量度量）

Revision ID: 005
Revises: 004
Create Date: 2026-08-28

背景:
  Copilot 打磨 A 阶段：度量闭环。记录每次建议 + 采纳行为 + 成本，
  支撑「采纳率 / 按意图采纳率 / 编辑量 / 单次成本」统计看板。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, table_name: str) -> bool:
    from sqlalchemy import inspect
    return inspect(bind).has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "copilot_logs"):
        op.create_table(
            "copilot_logs",
            sa.Column("log_id", sa.String(64), primary_key=True),
            sa.Column("session_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("agent_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("customer_message", sa.Text(), nullable=False, server_default=""),
            sa.Column("suggested_reply", sa.Text(), nullable=False, server_default=""),
            sa.Column("context_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("suggested_tools_json", sa.Text(), nullable=True),
            sa.Column("adopted", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("edited_delta", sa.Text(), nullable=False, server_default=""),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_cny", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.String(64), nullable=False),
            sa.Column("adopted_at", sa.String(64), nullable=True),
        )
        op.create_index("ix_copilot_logs_session_id", "copilot_logs", ["session_id"])
        op.create_index("ix_copilot_logs_created_at", "copilot_logs", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "copilot_logs"):
        op.drop_table("copilot_logs")