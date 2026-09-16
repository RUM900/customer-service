"""
copilot_logs 表新增 knowledge_refs_json — Copilot 知识库引用审计

Revision ID: 006
Revises: 005
Create Date: 2026-08-28

背景:
  C 阶段③知识库引用：记录每次 Copilot 建议引用的 FAQ 依据，
  用于追溯"话术引用了哪条政策"（防幻觉 + 复盘引用命中率）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    from sqlalchemy import inspect
    return column_name in [c["name"] for c in inspect(bind).get_columns(table_name)]


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "copilot_logs", "knowledge_refs_json"):
        op.add_column(
            "copilot_logs",
            sa.Column("knowledge_refs_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "copilot_logs", "knowledge_refs_json"):
        op.drop_column("copilot_logs", "knowledge_refs_json")