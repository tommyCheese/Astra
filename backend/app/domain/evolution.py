from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Set
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
IDENTITY_PATTERN = r"^[a-z0-9][a-z0-9_.:-]{0,239}$"
PARAMETER_PATH_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
TOOL_IDENTITY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")

ScalarValue: TypeAlias = Annotated[
    StrictBool | StrictInt | StrictFloat | StrictStr,
    Field(union_mode="left_to_right"),
]


class EvolutionDomainError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


class EvolutionCandidateType(str, Enum):
    procedure = "procedure"
    policy_recommendation = "policy_recommendation"


class EvolutionTarget(str, Enum):
    procedure = "procedure"
    planner = "planner"
    model_routing = "model_routing"
    memory_retrieval = "memory_retrieval"
    scheduling = "scheduling"


class EvolutionCandidateStatus(str, Enum):
    draft = "draft"
    evaluating = "evaluating"
    rejected = "rejected"
    approved = "approved"
    shadow = "shadow"
    canary = "canary"
    promoted = "promoted"
    rolled_back = "rolled_back"


class EvolutionSourceType(str, Enum):
    run = "run"
    turn = "turn"
    memory = "memory"
    artifact = "artifact"
    evaluation = "evaluation"
    case = "case"


class EvaluationCaseSplit(str, Enum):
    representative = "representative"
    held_out = "held_out"


class SafetyMetricDirection(str, Enum):
    higher_is_better = "higher_is_better"
    lower_is_better = "lower_is_better"


class EvolutionSourceReference(FrozenModel):
    source_type: EvolutionSourceType
    source_id: str = Field(min_length=1, max_length=240, pattern=IDENTITY_PATTERN)
    digest: str = Field(pattern=SHA256_PATTERN)


class EvolutionConstraint(FrozenModel):
    key: str = Field(min_length=1, max_length=120, pattern=PARAMETER_PATH_PATTERN)
    value: ScalarValue


class EvolutionParameterChange(FrozenModel):
    path: str = Field(min_length=3, max_length=160, pattern=PARAMETER_PATH_PATTERN)
    value: ScalarValue


class EvolutionCandidate(FrozenModel):
    schema_version: Literal[1] = 1
    candidate_key: str = Field(min_length=1, max_length=240, pattern=IDENTITY_PATTERN)
    revision: int = Field(default=1, ge=1)
    candidate_type: EvolutionCandidateType
    target: EvolutionTarget
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=40_000)
    source_refs: tuple[EvolutionSourceReference, ...] = Field(min_length=1)
    required_tools: tuple[str, ...] = ()
    environment_constraints: tuple[EvolutionConstraint, ...] = ()
    parameter_changes: tuple[EvolutionParameterChange, ...] = ()
    supersedes_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=240,
        pattern=IDENTITY_PATTERN,
    )

    @field_validator("required_tools")
    @classmethod
    def normalize_required_tools(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({value.strip().lower() for value in values if value.strip()}))
        if len(normalized) != len(values):
            raise ValueError("required_tools must be non-empty and unique")
        if any(not TOOL_IDENTITY_PATTERN.fullmatch(value) for value in normalized):
            raise ValueError("required_tools contains an invalid tool identity")
        return normalized

    @field_validator("source_refs")
    @classmethod
    def normalize_source_refs(cls, values: tuple[EvolutionSourceReference, ...]) -> tuple[EvolutionSourceReference, ...]:
        keys = [(item.source_type.value, item.source_id, item.digest) for item in values]
        if len(keys) != len(set(keys)):
            raise ValueError("source_refs must be unique")
        return tuple(
            sorted(
                values,
                key=lambda item: (item.source_type.value, item.source_id, item.digest),
            )
        )

    @field_validator("environment_constraints")
    @classmethod
    def normalize_constraints(cls, values: tuple[EvolutionConstraint, ...]) -> tuple[EvolutionConstraint, ...]:
        keys = [item.key for item in values]
        if len(keys) != len(set(keys)):
            raise ValueError("environment constraint keys must be unique")
        return tuple(sorted(values, key=lambda item: item.key))

    @field_validator("parameter_changes")
    @classmethod
    def normalize_parameter_changes(cls, values: tuple[EvolutionParameterChange, ...]) -> tuple[EvolutionParameterChange, ...]:
        paths = [item.path for item in values]
        if len(paths) != len(set(paths)):
            raise ValueError("parameter change paths must be unique")
        return tuple(sorted(values, key=lambda item: item.path))

    @model_validator(mode="after")
    def validate_candidate_shape(self) -> EvolutionCandidate:
        if self.candidate_type == EvolutionCandidateType.procedure:
            if self.target != EvolutionTarget.procedure:
                raise ValueError("procedure candidates must target procedure")
            if self.parameter_changes:
                raise ValueError("procedure candidates cannot contain parameter changes")
        else:
            if self.target == EvolutionTarget.procedure:
                raise ValueError("policy recommendations must target a tunable component")
            if not self.parameter_changes:
                raise ValueError("policy recommendations require parameter changes")
        if self.revision == 1 and self.supersedes_id is not None:
            raise ValueError("the first candidate revision cannot supersede another revision")
        if self.revision > 1 and self.supersedes_id is None:
            raise ValueError("later candidate revisions require supersedes_id")
        return self

    @property
    def digest(self) -> str:
        return _stable_digest(
            {
                "schema_version": self.schema_version,
                "candidate_key": self.candidate_key,
                "revision": self.revision,
                "candidate_type": self.candidate_type.value,
                "target": self.target.value,
                "title": self.title,
                "content": self.content,
                "source_refs": [item.model_dump(mode="json") for item in self.source_refs],
                "required_tools": list(self.required_tools),
                "environment_constraints": [item.model_dump(mode="json") for item in self.environment_constraints],
                "parameter_changes": [item.model_dump(mode="json") for item in self.parameter_changes],
                "supersedes_id": self.supersedes_id,
            }
        )


class EvolutionCandidateState(FrozenModel):
    candidate_digest: str = Field(pattern=SHA256_PATTERN)
    status: EvolutionCandidateStatus = EvolutionCandidateStatus.draft
    state_version: int = Field(default=1, ge=1)
    evaluation_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)


class EvaluationCaseResult(FrozenModel):
    case_id: str = Field(min_length=1, max_length=240, pattern=IDENTITY_PATTERN)
    case_digest: str = Field(pattern=SHA256_PATTERN)
    split: EvaluationCaseSplit
    baseline_score: float = Field(ge=0, le=1)
    candidate_score: float = Field(ge=0, le=1)
    candidate_safety_passed: bool = True


class EvaluationResultSummary(FrozenModel):
    sample_size: int = Field(ge=1)
    success_rate: float = Field(ge=0, le=1)
    mean_cost: float = Field(ge=0)
    mean_latency_ms: float = Field(ge=0)
    context_digest: str = Field(pattern=SHA256_PATTERN)


class SafetyMetricResult(FrozenModel):
    name: str = Field(min_length=1, max_length=120, pattern=IDENTITY_PATTERN)
    direction: SafetyMetricDirection
    baseline_value: float
    candidate_value: float

    @property
    def regressed(self) -> bool:
        if math.isclose(
            self.baseline_value,
            self.candidate_value,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            return False
        if self.direction == SafetyMetricDirection.higher_is_better:
            return self.candidate_value < self.baseline_value
        return self.candidate_value > self.baseline_value


class EvaluationThresholds(FrozenModel):
    minimum_sample_size: int = Field(default=10, ge=1)
    minimum_held_out_cases: int = Field(default=3, ge=1)
    max_success_rate_regression: float = Field(default=0, ge=0, le=1)
    max_cost_increase_ratio: float = Field(default=0.25, ge=0)
    max_latency_increase_ratio: float = Field(default=0.25, ge=0)


class EvaluationManifest(FrozenModel):
    schema_version: Literal[1] = 1
    version: int = Field(default=1, ge=1)
    candidate_digest: str = Field(pattern=SHA256_PATTERN)
    evaluator_id: str = Field(min_length=1, max_length=240, pattern=IDENTITY_PATTERN)
    evaluator_version: str = Field(min_length=1, max_length=120)
    suite_id: str = Field(min_length=1, max_length=240, pattern=IDENTITY_PATTERN)
    suite_version: str = Field(min_length=1, max_length=120)
    suite_digest: str = Field(pattern=SHA256_PATTERN)
    baseline: EvaluationResultSummary
    candidate: EvaluationResultSummary
    cases: tuple[EvaluationCaseResult, ...] = Field(min_length=2)
    safety_metrics: tuple[SafetyMetricResult, ...] = Field(min_length=1)
    thresholds: EvaluationThresholds

    @field_validator("cases")
    @classmethod
    def normalize_cases(cls, values: tuple[EvaluationCaseResult, ...]) -> tuple[EvaluationCaseResult, ...]:
        ids = [item.case_id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation case IDs must be unique")
        return tuple(sorted(values, key=lambda item: (item.split.value, item.case_id)))

    @field_validator("safety_metrics")
    @classmethod
    def normalize_safety_metrics(cls, values: tuple[SafetyMetricResult, ...]) -> tuple[SafetyMetricResult, ...]:
        names = [item.name for item in values]
        if len(names) != len(set(names)):
            raise ValueError("safety metric names must be unique")
        return tuple(sorted(values, key=lambda item: item.name))

    @model_validator(mode="after")
    def validate_comparable_results(self) -> EvaluationManifest:
        if self.baseline.context_digest != self.candidate.context_digest:
            raise ValueError("baseline and candidate execution contexts must match")
        if self.baseline.sample_size != self.candidate.sample_size:
            raise ValueError("baseline and candidate sample sizes must match")
        if self.baseline.sample_size != len(self.cases):
            raise ValueError("result sample size must equal the number of case results")
        held_out = sum(item.split == EvaluationCaseSplit.held_out for item in self.cases)
        representative = sum(item.split == EvaluationCaseSplit.representative for item in self.cases)
        if not representative:
            raise ValueError("evaluation requires representative cases")
        if held_out < self.thresholds.minimum_held_out_cases:
            raise ValueError("evaluation has too few held-out cases")
        if len(self.cases) < self.thresholds.minimum_sample_size:
            raise ValueError("evaluation has too few total cases")
        return self

    @property
    def digest(self) -> str:
        return _stable_digest(
            {
                "schema_version": self.schema_version,
                "version": self.version,
                "candidate_digest": self.candidate_digest,
                "evaluator_id": self.evaluator_id,
                "evaluator_version": self.evaluator_version,
                "suite_id": self.suite_id,
                "suite_version": self.suite_version,
                "suite_digest": self.suite_digest,
                "baseline": self.baseline.model_dump(mode="json"),
                "candidate": self.candidate.model_dump(mode="json"),
                "cases": [item.model_dump(mode="json") for item in self.cases],
                "safety_metrics": [item.model_dump(mode="json") for item in self.safety_metrics],
                "thresholds": self.thresholds.model_dump(mode="json"),
            }
        )


class EvaluationIssue(FrozenModel):
    code: str
    message: str
    metric: str | None = None
    baseline: float | None = None
    candidate: float | None = None
    limit: float | None = None


class EvaluationDecision(FrozenModel):
    manifest_digest: str = Field(pattern=SHA256_PATTERN)
    passed: bool
    success_rate_delta: float
    cost_increase_ratio: float
    latency_increase_ratio: float
    issues: tuple[EvaluationIssue, ...] = ()


class AuthorityIssue(FrozenModel):
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class TunableParameterSpec:
    value_kind: Literal["integer", "number", "string"]
    minimum: float | None = None
    maximum: float | None = None
    choices: frozenset[str] = frozenset()


TUNABLE_PARAMETER_SPECS = MappingProxyType(
    {
        EvolutionTarget.planner: MappingProxyType(
            {
                "planner.max_plan_depth": TunableParameterSpec("integer", 1, 12),
                "planner.max_candidate_strategies": TunableParameterSpec("integer", 1, 8),
                "planner.max_replans": TunableParameterSpec("integer", 0, 8),
            }
        ),
        EvolutionTarget.model_routing: MappingProxyType(
            {
                "model_routing.reasoning_effort": TunableParameterSpec(
                    "string",
                    choices=frozenset({"fast", "balanced", "deep"}),
                ),
                "model_routing.strategy": TunableParameterSpec(
                    "string",
                    choices=frozenset({"quality", "balanced", "cost", "latency"}),
                ),
            }
        ),
        EvolutionTarget.memory_retrieval: MappingProxyType(
            {
                "memory_retrieval.max_items": TunableParameterSpec("integer", 1, 32),
                "memory_retrieval.token_budget": TunableParameterSpec("integer", 128, 32_768),
                "memory_retrieval.min_confidence": TunableParameterSpec("number", 0, 1),
                "memory_retrieval.lexical_weight": TunableParameterSpec("number", 0, 1),
                "memory_retrieval.recency_weight": TunableParameterSpec("number", 0, 1),
                "memory_retrieval.semantic_weight": TunableParameterSpec("number", 0, 1),
            }
        ),
        EvolutionTarget.scheduling: MappingProxyType(
            {
                "scheduling.max_parallel_nodes": TunableParameterSpec("integer", 1, 16),
                "scheduling.provider_concurrency_limit": TunableParameterSpec("integer", 1, 64),
                "scheduling.capability_concurrency_limit": TunableParameterSpec("integer", 1, 32),
            }
        ),
    }
)

PROTECTED_AUTHORITY_PREFIXES = (
    "approval.",
    "credential.",
    "evidence.",
    "identity.",
    "permission.",
    "retention.",
    "sandbox.",
    "security.",
    "skill.",
    "tool.",
    "tool_catalog.",
    "agent_profile.",
)

_AUTHORITY_RELAXATION_PATTERNS = (
    re.compile(
        r"\b(?:bypass|disable|skip|remove|weaken|relax|ignore|lower)\b.{0,48}"
        r"\b(?:approval|permission|sandbox|credential|security|retention|"
        r"evidence|verification)\b",
        re.I | re.S,
    ),
    re.compile(
        r"(?:绕过|禁用|跳过|移除|削弱|放宽|忽略|降低).{0,24}"
        r"(?:审批|权限|沙箱|凭证|安全|保留|证据|验证)",
        re.S,
    ),
    re.compile(
        r"\b(?:grant|expand|elevate)\b.{0,48}"
        r"\b(?:permission|authority|credential|tool availability)\b",
        re.I | re.S,
    ),
    re.compile(r"(?:授予|扩大|提升).{0,24}(?:权限|授权|凭证|工具可用性)", re.S),
)

_NEGATION_SUFFIX = re.compile(
    r"(?:never|must\s+not|do\s+not|cannot|can't|不得|禁止|不能)\s*$",
    re.I,
)


def validate_candidate_authority(
    candidate: EvolutionCandidate,
    *,
    available_tools: Set[str],
) -> tuple[AuthorityIssue, ...]:
    issues: list[AuthorityIssue] = []
    unavailable = sorted(set(candidate.required_tools) - set(available_tools))
    for tool in unavailable:
        issues.append(
            AuthorityIssue(
                code="evolution.tool_unavailable",
                message="Candidate references a tool outside the current executable ceiling.",
                path=tool,
            )
        )

    if candidate.candidate_type == EvolutionCandidateType.policy_recommendation:
        allowed = TUNABLE_PARAMETER_SPECS.get(candidate.target, {})
        for change in candidate.parameter_changes:
            if change.path.startswith(PROTECTED_AUTHORITY_PREFIXES):
                issues.append(
                    AuthorityIssue(
                        code="evolution.protected_authority",
                        message="Candidate attempts to modify a protected authority boundary.",
                        path=change.path,
                    )
                )
                continue
            spec = allowed.get(change.path)
            if spec is None:
                issues.append(
                    AuthorityIssue(
                        code="evolution.parameter_not_tunable",
                        message="Candidate parameter is outside the explicit target allowlist.",
                        path=change.path,
                    )
                )
                continue
            message = _parameter_violation(change.value, spec)
            if message:
                issues.append(
                    AuthorityIssue(
                        code="evolution.parameter_out_of_bounds",
                        message=message,
                        path=change.path,
                    )
                )

    if _requests_authority_relaxation(candidate.content):
        issues.append(
            AuthorityIssue(
                code="evolution.protected_authority_instruction",
                message="Candidate content requests relaxation of a protected authority boundary.",
            )
        )
    return tuple(issues)


def assert_candidate_authority(
    candidate: EvolutionCandidate,
    *,
    available_tools: Set[str],
) -> None:
    issues = validate_candidate_authority(
        candidate,
        available_tools=available_tools,
    )
    if issues:
        raise EvolutionDomainError(
            "EVOLUTION_AUTHORITY_VIOLATION",
            "Evolution candidate exceeds its governed authority boundary.",
            {"issues": [item.model_dump(mode="json") for item in issues]},
        )


def _parameter_violation(value: ScalarValue, spec: TunableParameterSpec) -> str | None:
    if spec.value_kind == "integer":
        if type(value) is not int:
            return "Parameter requires an integer value."
        numeric = float(value)
    elif spec.value_kind == "number":
        if type(value) not in {int, float}:
            return "Parameter requires a numeric value."
        numeric = float(value)
    else:
        if not isinstance(value, str) or value not in spec.choices:
            return "Parameter value is outside the allowed choices."
        return None
    if spec.minimum is not None and numeric < spec.minimum:
        return "Parameter value is below the governed minimum."
    if spec.maximum is not None and numeric > spec.maximum:
        return "Parameter value exceeds the governed maximum."
    return None


def _requests_authority_relaxation(content: str) -> bool:
    for pattern in _AUTHORITY_RELAXATION_PATTERNS:
        for match in pattern.finditer(content):
            prefix = content[max(0, match.start() - 24) : match.start()]
            if not _NEGATION_SUFFIX.search(prefix):
                return True
    return False


def _stable_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
