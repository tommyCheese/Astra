"""Final-answer normalization and completion verification."""

from __future__ import annotations

from typing import Any

from app.common.schemas.agent.run_result import (
    FinalAnswer,
    ValidationIssue,
    ValidationOutcome,
    VerificationReport,
)
from app.common.schemas.agent.types import AssuranceLevel

INVALID_ARTIFACT_REFERENCE_WARNING = "已移除无效或不可访问的工具输出引用。"


def normalize_final_answer_artifact_references(
    final_answer: FinalAnswer,
    artifacts: list[Any],
) -> tuple[FinalAnswer, int, list[str]]:
    """Keep only accessible artifacts from the current Run."""
    allowed_ids = {
        str(artifact.id)
        for artifact in artifacts
        if artifact.security_status == "verified" and artifact.storage_key
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
        final_answer.model_copy(
            update={"findings": normalized_findings, "verification_notes": notes}
        ),
        invalid_count,
        referenced_ids,
    )


def quick_workspace_change_completes_goal(
    goal: str,
    workspace_changes: list[dict[str, Any]],
) -> bool:
    if not workspace_changes:
        return False
    normalized_goal = goal.casefold()
    if _requires_multiple_steps(normalized_goal):
        return False
    return _requested_file_changed(normalized_goal, workspace_changes) or (
        _workspace_deletion_requested(normalized_goal)
        and all(change.get("kind") == "deleted" for change in workspace_changes)
    )


def _requires_multiple_steps(normalized_goal: str) -> bool:
    return any(
        marker in normalized_goal
        for marker in (
            "图表",
            "绘图",
            "可视化",
            "渲染",
            "图片",
            "chart",
            "plot",
            "visuali",
            "render",
            "image",
        )
    )


def _requested_file_changed(
    normalized_goal: str,
    workspace_changes: list[dict[str, Any]],
) -> bool:
    filenames = (
        str(change.get("path") or "").rsplit("/", 1)[-1].casefold() for change in workspace_changes
    )
    return any(filename and filename in normalized_goal for filename in filenames)


def _workspace_deletion_requested(normalized_goal: str) -> bool:
    return any(
        marker in normalized_goal
        for marker in ("删除工作区", "清空工作区", "delete workspace", "clear workspace")
    )


class CompletionVerificationStage:
    def verify(
        self,
        final_answer: FinalAnswer,
        evidence_pack: dict[str, Any],
        *,
        validation_outcomes: list[ValidationOutcome] | None = None,
        invalid_artifact_references: int = 0,
        assurance_level: AssuranceLevel = AssuranceLevel.full,
    ) -> VerificationReport:
        fetched_sources = evidence_pack.get("fetched_sources", [])
        low_quality = [
            source for source in fetched_sources if float(source.get("quality_score") or 0) < 0.5
        ]
        outcomes = [
            *(validation_outcomes or []),
            self._artifact_validation(invalid_artifact_references),
        ]
        notes = self._verification_notes(final_answer, fetched_sources, outcomes)
        return VerificationReport(
            status=self._status(outcomes),
            assurance_level=assurance_level,
            source_count=len(final_answer.sources),
            caveat_count=len(final_answer.caveats),
            low_quality_sources=low_quality,
            failed_sources=evidence_pack.get("failed_sources", []),
            memory_references=final_answer.memory_references,
            invalid_artifact_references=invalid_artifact_references,
            notes=notes,
            validation_outcomes=outcomes,
        )

    @staticmethod
    def _status(outcomes: list[ValidationOutcome]) -> str:
        if any(not outcome.passed and outcome.blocking for outcome in outcomes):
            return "failed"
        has_warnings = any(
            outcome.warnings or any(issue.severity == "warning" for issue in outcome.issues)
            for outcome in outcomes
        )
        return "completed_with_warnings" if has_warnings else "completed"

    @staticmethod
    def _artifact_validation(invalid_count: int) -> ValidationOutcome:
        warnings = [INVALID_ARTIFACT_REFERENCE_WARNING] if invalid_count else []
        issues = (
            [
                ValidationIssue(
                    code="artifact_reference_invalid",
                    message=INVALID_ARTIFACT_REFERENCE_WARNING,
                    severity="warning",
                    details={"invalid_count": invalid_count},
                )
            ]
            if invalid_count
            else []
        )
        return ValidationOutcome(
            validator="artifact_reference",
            passed=True,
            blocking=False,
            issues=issues,
            warnings=warnings,
        )

    @staticmethod
    def _verification_notes(
        final_answer: FinalAnswer,
        fetched_sources: list[dict[str, Any]],
        outcomes: list[ValidationOutcome],
    ) -> list[str]:
        notes = list(final_answer.verification_notes)
        for outcome in outcomes:
            notes.extend(outcome.warnings)
            notes.extend(issue.message for issue in outcome.issues)
        if (
            fetched_sources
            and final_answer.sources
            and not any(not outcome.passed and outcome.blocking for outcome in outcomes)
        ):
            notes.append("至少一个抓取来源支撑了最终答案。")
        return list(dict.fromkeys(notes))
