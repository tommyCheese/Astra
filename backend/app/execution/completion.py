"""Completion policy shared by root and delegated execution runtimes."""

from app.schemas.agent.execution_state import CompletionDecision
from app.schemas.agent.run_result import ValidationOutcome
from app.schemas.agent.types import TerminalState


class BasicCompletionGate:
    def evaluate(
        self,
        *,
        validation_outcomes: list[ValidationOutcome],
    ) -> CompletionDecision:
        blocking = [
            outcome.validator
            for outcome in validation_outcomes
            if not outcome.passed and outcome.blocking
        ]
        warnings = self._warnings(validation_outcomes)
        if blocking:
            return CompletionDecision(
                state=TerminalState.blocked,
                reason="基础保障存在阻塞问题。",
                unmet_criteria=[f"validator:{validator}" for validator in blocking],
                warnings=warnings,
            )
        return CompletionDecision(
            state=(TerminalState.completed_with_warnings if warnings else TerminalState.completed),
            reason="快速回答已完成基础保障检查。",
            warnings=warnings,
        )

    @staticmethod
    def _warnings(validation_outcomes: list[ValidationOutcome]) -> list[str]:
        return list(
            dict.fromkeys(
                [warning for outcome in validation_outcomes for warning in outcome.warnings]
                + [
                    issue.message
                    for outcome in validation_outcomes
                    for issue in outcome.issues
                    if issue.severity == "warning"
                ]
            )
        )
