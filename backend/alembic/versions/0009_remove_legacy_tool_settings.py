"""Remove the legacy chart_render tool-state alias.

Revision ID: 0009_remove_legacy_tool_settings
Revises: 0008_dynamic_tool_provider_settings
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_remove_legacy_tool_settings"
down_revision: str | Sequence[str] | None = "0008_dynamic_tool_provider_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM tool_settings WHERE tool_name = 'chart_render' "
        "AND EXISTS (SELECT 1 FROM tool_settings WHERE tool_name = 'chart.render')"
    )
    op.execute(
        "UPDATE tool_settings SET tool_name = 'chart.render' "
        "WHERE tool_name = 'chart_render'"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM tool_settings WHERE tool_name = 'chart.render' "
        "AND EXISTS (SELECT 1 FROM tool_settings WHERE tool_name = 'chart_render')"
    )
    op.execute(
        "UPDATE tool_settings SET tool_name = 'chart_render' "
        "WHERE tool_name = 'chart.render'"
    )
