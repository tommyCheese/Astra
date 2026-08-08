"""Remove active settings for retired built-in Web tools.

Revision ID: 0010_remove_builtin_web_tools
Revises: 0009_remove_legacy_tool_settings
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_remove_builtin_web_tools"
down_revision: str | Sequence[str] | None = "0009_remove_legacy_tool_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM tool_settings WHERE tool_name IN ('web_search', 'web_fetch')")
    op.execute("DELETE FROM tool_provider_settings WHERE provider_id = 'astra.web'")


def downgrade() -> None:
    op.execute(
        "INSERT INTO tool_settings (tool_name, enabled, created_at, updated_at) "
        "SELECT 'web_search', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "WHERE NOT EXISTS (SELECT 1 FROM tool_settings WHERE tool_name = 'web_search')"
    )
    op.execute(
        "INSERT INTO tool_settings (tool_name, enabled, created_at, updated_at) "
        "SELECT 'web_fetch', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "WHERE NOT EXISTS (SELECT 1 FROM tool_settings WHERE tool_name = 'web_fetch')"
    )
    op.execute(
        "INSERT INTO tool_provider_settings "
        "(provider_id, enabled, configuration, configuration_revision, created_at, updated_at) "
        "SELECT 'astra.web', TRUE, '{}', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "WHERE NOT EXISTS ("
        "SELECT 1 FROM tool_provider_settings WHERE provider_id = 'astra.web'"
        ")"
    )
