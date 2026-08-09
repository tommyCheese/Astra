"""Loop transition validation and no-progress detection."""

from collections.abc import Iterable
from typing import Any

from app.common.schemas.agent.execution_state import NodeResult

TRANSITIONS: dict[str, set[str]] = {
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
    "completion_gate": {
        "select_action",
        "plan",
        "finalize_response",
        "waiting_user",
        "blocked",
        "failed",
    },
    "finalize_response": {"completed", "completed_with_warnings", "blocked", "failed"},
}

PATCH_AUTHORITIES: dict[str, set[str]] = {
    "compile_policy": {"reasoning_policy"},
    "build_contract": {"task_contract", "waiting_state"},
    "plan": {"plan"},
    "evaluate": {"evaluations"},
    "update_state": {"observations", "accepted_facts", "task_contract", "budget_usage"},
    "apply_reflection": {
        "plan",
        "accepted_facts",
        "task_contract",
        "terminal_intent",
        "waiting_state",
    },
    "completion_gate": {"terminal_intent", "terminal_reason", "waiting_state"},
}

ERROR_EXITS: dict[str, set[str]] = {
    "model_output": {"select_action", "blocked"},
    "policy_denied": {"waiting_user", "blocked"},
    "tool_transient": {"select_action", "apply_reflection", "blocked"},
    "tool_permanent": {"apply_reflection", "plan", "blocked"},
    "state_conflict": {"select_action", "blocked"},
    "validator_failure": {"apply_reflection", "plan", "blocked", "finalize_response"},
    "budget_exhausted": {"completion_gate"},
    "runtime_internal": {"failed"},
}


def validate_transition(current_node: str, result: NodeResult) -> None:
    """Validate the fixed runtime graph without manufacturing a service object."""
    if result.next_node not in TRANSITIONS.get(current_node, set()):
        raise RuntimeError(f"Invalid transition: {current_node} -> {result.next_node}")
    allowed = PATCH_AUTHORITIES.get(current_node, set())
    unauthorized = set(result.state_patch) - allowed
    if unauthorized:
        raise RuntimeError(f"Node {current_node} cannot patch: {', '.join(sorted(unauthorized))}")
    if result.error:
        category = result.error.get("category", "runtime_internal")
        if result.next_node not in ERROR_EXITS.get(category, {"failed"}):
            raise RuntimeError(f"Error {category} cannot exit to {result.next_node}")


def record_progress_signature(
    signatures: list[str],
    *,
    evidence_refs: Iterable[str],
    criterion_changes: dict[str, Any],
    completed_steps: Iterable[str],
    plan_version: int,
    threshold: int = 3,
) -> bool:
    signature = repr(
        (
            sorted(evidence_refs),
            sorted(criterion_changes.items()),
            sorted(completed_steps),
            plan_version,
        )
    )
    signatures.append(signature)
    return len(signatures) >= threshold and len(set(signatures[-threshold:])) == 1
