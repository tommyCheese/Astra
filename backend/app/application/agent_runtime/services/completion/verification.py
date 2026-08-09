"""Final-answer normalization and completion verification."""

from __future__ import annotations

from typing import Any

from app.common.schemas.agent.run_result import (
    AgentAnswerVerificationReport,
    AgentFinalAnswer,
    AgentValidationIssue,
    AgentValidationOutcome,
)
from app.common.schemas.agent.types import AssuranceLevel

INVALID_ARTIFACT_REFERENCE_WARNING = "已移除无效或不可访问的工具输出引用。"


def normalize_final_answer_artifact_references(
    final_answer: AgentFinalAnswer,
    artifacts: list[Any],
) -> tuple[AgentFinalAnswer, int, list[str]]:
    """Keep only accessible artifacts from the current Run."""
    allowed_ids = {
        str(artifact.id) for artifact in artifacts if artifact.security_status == "verified" and artifact.storage_key
    }
    invalid_count = 0
    referenced_ids: list[str] = []
    normalized_findings = []
    for finding in final_answer.findings:
        valid_ids = []
        for artifact_id in dict.fromkeys(finding.artifact_ids):
            if artifact_id not in allowed_ids:
                invalid_count += 1
            else:
                valid_ids.append(artifact_id)
                if artifact_id not in referenced_ids:
                    referenced_ids.append(artifact_id)
        normalized_findings.append(finding.model_copy(update={"artifact_ids": valid_ids}))
    notes = list(final_answer.verification_notes)
    if invalid_count and INVALID_ARTIFACT_REFERENCE_WARNING not in notes:
        notes.append(INVALID_ARTIFACT_REFERENCE_WARNING)
    return (
        final_answer.model_copy(update={"findings": normalized_findings, "verification_notes": notes}),
        invalid_count,
        referenced_ids,
    )


def verify_completion(
    final_answer: AgentFinalAnswer,
    evidence_pack: dict[str, Any],
    *,
    validation_outcomes: list[AgentValidationOutcome] | None = None,
    invalid_artifact_references: int = 0,
    assurance_level: AssuranceLevel = AssuranceLevel.full,
) -> AgentAnswerVerificationReport:
    fetched_sources = evidence_pack.get("fetched_sources", [])
    low_quality = list(filter(lambda source: float(source.get("quality_score") or 0) < 0.5, fetched_sources))
    outcomes = [
        *(validation_outcomes or []),
        _artifact_validation(invalid_artifact_references),
    ]
    notes, blocking, warnings = _verification_flags(final_answer, outcomes)
    if all((bool(fetched_sources), bool(final_answer.sources), not blocking)):
        notes.append("至少一个抓取来源支撑了最终答案。")
    return AgentAnswerVerificationReport(
        status="failed" if blocking else "completed_with_warnings" if warnings else "completed",
        assurance_level=assurance_level,
        source_count=len(final_answer.sources),
        caveat_count=len(final_answer.caveats),
        low_quality_sources=low_quality,
        failed_sources=evidence_pack.get("failed_sources", []),
        memory_references=final_answer.memory_references,
        invalid_artifact_references=invalid_artifact_references,
        notes=list(dict.fromkeys(notes)),
        validation_outcomes=outcomes,
    )


def _verification_flags(final_answer: AgentFinalAnswer, outcomes: list[AgentValidationOutcome]) -> tuple[list[str], bool, bool]:
    notes = list(final_answer.verification_notes)
    blocking = False
    warnings = False
    for outcome in outcomes:
        notes.extend(outcome.warnings)
        blocking |= not outcome.passed and outcome.blocking
        warnings |= bool(outcome.warnings)
        for issue in outcome.issues:
            notes.append(issue.message)
            warnings |= issue.severity == "warning"
    return notes, blocking, warnings


def _artifact_validation(invalid_count: int) -> AgentValidationOutcome:
    warnings = [INVALID_ARTIFACT_REFERENCE_WARNING] if invalid_count else []
    issues = (
        [
            AgentValidationIssue(
                code="artifact_reference_invalid",
                message=INVALID_ARTIFACT_REFERENCE_WARNING,
                severity="warning",
                details={"invalid_count": invalid_count},
            )
        ]
        if invalid_count
        else []
    )
    return AgentValidationOutcome(
        validator="artifact_reference",
        passed=True,
        blocking=False,
        issues=issues,
        warnings=warnings,
    )
