from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent import AnswerMode


class SkillCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)


class SkillImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(default="skill.zip", max_length=240)
    content_base64: str


class SkillCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)


class SkillFileOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["write", "delete", "move"] = "write"
    path: str
    target: str | None = None
    content: str | None = None
    content_base64: str | None = None


class SkillDraftUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_token: str
    operations: list[SkillFileOperation] = Field(min_length=1, max_length=256)


class SkillPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_token: str


class SkillStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class SkillRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


class SkillTestRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_token: str
    goal: str = Field(min_length=1)
    answer_mode: AnswerMode


class SkillDiagnosticView(BaseModel):
    code: str
    message: str
    severity: str
    path: str | None = None
    line: int | None = None
    column: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SkillFileView(BaseModel):
    path: str
    uri: str
    digest: str
    size_bytes: int
    media_type: str
    kind: str
    text: bool
    readonly: bool


class SkillRevisionView(BaseModel):
    id: str
    version: int
    digest: str
    published_at: str | None = None
    revoked_at: str | None = None
    test_only: bool = False
    diagnostics: list[SkillDiagnosticView] = Field(default_factory=list)


class SkillRevisionDetailView(SkillRevisionView):
    files: list[SkillFileView] = Field(default_factory=list)


class SkillSummaryView(BaseModel):
    id: str
    name: str
    qualified_identity: str
    origin: Literal["builtin", "custom"]
    description: str
    enabled: bool
    readonly: bool
    lifecycle_state: str
    active_revision: SkillRevisionView | None = None
    draft_revision_token: str | None = None
    diagnostics: list[SkillDiagnosticView] = Field(default_factory=list)
    created_at: str
    updated_at: str


class SkillDetailView(SkillSummaryView):
    files: list[SkillFileView] = Field(default_factory=list)
    requested_tool_patterns: list[str] = Field(default_factory=list)
    compatibility: str | None = None


class SkillDraftFilesView(BaseModel):
    skill_id: str
    revision_token: str
    readonly: bool
    files: list[SkillFileView]
    diagnostics: list[SkillDiagnosticView] = Field(default_factory=list)


class SkillFileContentView(BaseModel):
    path: str
    uri: str
    media_type: str
    digest: str
    text: bool
    content: str | None = None
    content_base64: str | None = None
    readonly: bool


class SkillValidationView(BaseModel):
    valid: bool
    publishable: bool
    digest: str | None = None
    diagnostics: list[SkillDiagnosticView] = Field(default_factory=list)


class SkillDiffView(BaseModel):
    skill_id: str
    draft_revision_token: str | None = None
    active_revision_id: str | None = None
    files: list[dict[str, Any]] = Field(default_factory=list)


class SkillRevisionDiffView(BaseModel):
    skill_id: str
    base_revision_id: str
    target_revision_id: str
    base_version: int
    target_version: int
    patch: str
    files: list[dict[str, Any]] = Field(default_factory=list)


class SkillCatalogView(BaseModel):
    digest: str
    truncated: bool
    skills: list[dict[str, Any]]


class RunSkillsView(BaseModel):
    run_id: str
    catalog_digest: str
    answer_mode: str
    draft_test: bool
    catalog: list[dict[str, Any]]
    activations: list[dict[str, Any]]
    resource_reads: list[dict[str, Any]]
    attributed_actions: list[dict[str, Any]] = Field(default_factory=list)
    plan_bindings: list[dict[str, Any]] = Field(default_factory=list)
