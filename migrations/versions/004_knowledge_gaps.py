"""
新增 knowledge_gaps 表 — 知识盲区记录（知识库运营闭环）

Revision ID: 004
Revises: 003
Create Date: 2026-08-28

背景:
  知识库自闭环需要记录 FAQ 未命中 / 转人工的高频盲区问题，
  聚类生成《待补充 FAQ 建议清单》，实现运营数据反哺。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, table_name: str) -> bool:
    from sqlalchemy import inspect
    return inspect(bind).has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "knowledge_gaps"):
        op.create_table(
            "knowledge_gaps",
            sa.Column("gap_id", sa.String(64), primary_key=True),
            sa.Column("session_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("query", sa.String(512), nullable=False, server_default=""),
            sa.Column("route", sa.String(32), nullable=False, server_default="faq_answer"),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.String(64), nullable=False),
        )
        op.create_index("ix_knowledge_gaps_session_id", "knowledge_gaps", ["session_id"])
        op.create_index("ix_knowledge_gaps_query", "knowledge_gaps", ["query"])
        op.create_index("ix_knowledge_gaps_created_at", "knowledge_gaps", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "knowledge_gaps"):
        op.drop_table("knowledge_gaps")