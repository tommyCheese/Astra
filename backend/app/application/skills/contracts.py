from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SKILL_PACKAGE_SCHEMA_VERSION = 1


class SkillOrigin(str, Enum):
    builtin = "builtin"
    custom = "custom"


class SkillDiagnostic(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "error", "critical"] = "error"
    path: str | None = None
    line: int | None = None
    column: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SkillFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")


class SkillResource(BaseModel):
    path: str
    digest: str
    size_bytes: int
    media_type: str
    kind: Literal["instructions", "script", "reference", "asset", "other"]
    text: bool


class SkillPackage(BaseModel):
    schema_version: Literal[1] = SKILL_PACKAGE_SCHEMA_VERSION
    origin: SkillOrigin
    qualified_identity: str
    frontmatter: SkillFrontmatter
    instructions: str
    digest: str
    resources: list[SkillResource]
    diagnostics: list[SkillDiagnostic] = Field(default_factory=list)
    requested_tool_patterns: list[str] = Field(default_factory=list)

    @property
    def publishable(self) -> bool:
        return not any(item.severity in {"error", "critical"} for item in self.diagnostics)


class SkillCatalogEntry(BaseModel):
    qualified_identity: str
    name: str
    description: str
    origin: SkillOrigin
    revision_id: str
    digest: str
    compatibility: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    requested_tool_patterns: list[str] = Field(default_factory=list)
    resources: list[SkillResource] = Field(default_factory=list)
    instructions_blob: str
    revoked: bool = False


class SkillActivation(BaseModel):
    qualified_identity: str
    revision_id: str
    digest: str
    initiator: Literal["explicit", "model", "draft_test"]
    reason: str
    activated_at: str
