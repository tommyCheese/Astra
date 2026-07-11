from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional

from app.schemas.agent import AgentObservation, NodeResult


TRANSITIONS: Dict[str, set[str]] = {
    "init": {"compile_policy"},
    "compile_policy": {"build_contract", "failed"},
    "build_contract": {"plan", "waiting_user", "failed"},
    "plan": {"select_action", "waiting_user", "blocked", "failed"},
    "select_action": {"policy_gate", "completion_gate", "waiting_user", "blocked"},
    "policy_gate": {"execute", "waiting_user", "blocked"},
    "execute": {"normalize_observation", "failed"},
    "normalize_observation": {"evaluate", "failed"},
    "evaluate": {"update_state", "failed"},
    "update_state": {"reflection_gate", "failed"},
    "reflection_gate": {"apply_reflection", "completion_gate"},
    "apply_reflection": {"select_action", "completion_gate", "waiting_user", "blocked"},
    "completion_gate": {"select_action", "plan", "finalize_response", "waiting_user", "blocked", "failed"},
    "finalize_response": {"completed", "completed_with_warnings", "blocked", "failed"},
}

PATCH_AUTHORITIES: Dict[str, set[str]] = {
    "compile_policy": {"reasoning_policy"},
    "build_contract": {"task_contract", "waiting_state"},
    "plan": {"plan"},
    "evaluate": {"evaluations"},
    "update_state": {"observations", "accepted_facts", "task_contract", "budget_usage"},
    "apply_reflection": {"plan", "accepted_facts", "task_contract", "terminal_intent", "waiting_state"},
    "completion_gate": {"terminal_intent", "terminal_reason", "waiting_state"},
}

ERROR_EXITS: Dict[str, set[str]] = {
    "model_output": {"select_action", "blocked"},
    "policy_denied": {"waiting_user", "blocked"},
    "tool_transient": {"select_action", "apply_reflection", "blocked"},
    "tool_permanent": {"apply_reflection", "plan", "blocked"},
    "state_conflict": {"select_action", "blocked"},
    "validator_failure": {"apply_reflection", "plan", "blocked", "finalize_response"},
    "budget_exhausted": {"completion_gate"},
    "runtime_internal": {"failed"},
}


class InvalidTransition(RuntimeError):
    pass


class LoopOrchestrator:
    def validate_result(self, current_node: str, result: NodeResult) -> None:
        if result.next_node not in TRANSITIONS.get(current_node, set()):
            raise InvalidTransition(f"Invalid transition: {current_node} -> {result.next_node}")
        allowed = PATCH_AUTHORITIES.get(current_node, set())
        unauthorized = set(result.state_patch) - allowed
        if unauthorized:
            raise InvalidTransition(f"Node {current_node} cannot patch: {', '.join(sorted(unauthorized))}")
        if result.error:
            category = result.error.get("category", "runtime_internal")
            if result.next_node not in ERROR_EXITS.get(category, {"failed"}):
                raise InvalidTransition(f"Error {category} cannot exit to {result.next_node}")

    def recovery_action(self, *, phase: str, idempotent: bool, result_recorded: bool) -> str:
        if result_recorded:
            return "replay_result"
        if phase == "prepared":
            return "execute"
        if phase == "executing" and idempotent:
            return "retry_same_idempotency_key"
        if phase == "executing":
            return "waiting_user"
        return "blocked"


class ObservationNormalizer:
    def normalize(self, source: str, *, status: str, summary: str, data: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None) -> AgentObservation:
        kinds = {
            "tool": "tool_result" if status == "succeeded" else "tool_error",
            "user": "user_response",
            "approval": "approval_result",
            "validator": "validator_result",
        }
        return AgentObservation(kind=kinds.get(source, source), status=status, summary=summary, data=data or {}, error=error)


@dataclass
class NoProgressDetector:
    threshold: int = 3
    signatures: list[str] = field(default_factory=list)

    def record(self, *, evidence_refs: Iterable[str], criterion_changes: Dict[str, Any], completed_steps: Iterable[str], plan_version: int) -> bool:
        signature = repr((sorted(evidence_refs), sorted(criterion_changes.items()), sorted(completed_steps), plan_version))
        self.signatures.append(signature)
        return len(self.signatures) >= self.threshold and len(set(self.signatures[-self.threshold:])) == 1
