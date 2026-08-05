from __future__ import annotations

from dataclasses import dataclass

from app.common.schemas.agent.run_policy import EXECUTABLE_SUBAGENT_COHORTS, EffectiveSubagentPolicy


@dataclass(frozen=True, slots=True)
class SubagentExecutionEligibility:
    executable: bool
    reason: str | None = None

    @property
    def message(self) -> str:
        return {
            "swarm_disabled": "Swarm / Subagents was disabled by the user",
            "policy_disabled": "Subagent execution is disabled by the frozen Run policy",
            "kill_switch": "Subagent execution is disabled by the frozen Run policy",
            "rollout_ineligible": "Subagent execution is unavailable for this rollout cohort",
        }.get(self.reason, "Subagent execution is unavailable")


def subagent_execution_eligibility(
    policy: EffectiveSubagentPolicy,
    *,
    live_swarm_enabled: bool,
) -> SubagentExecutionEligibility:
    """Evaluate the shared live gate for both quick and trusted root Runs."""
    if not live_swarm_enabled:
        return SubagentExecutionEligibility(False, "swarm_disabled")
    if not policy.enabled:
        return SubagentExecutionEligibility(False, "policy_disabled")
    if policy.kill_switch:
        return SubagentExecutionEligibility(False, "kill_switch")
    if policy.rollout_cohort not in EXECUTABLE_SUBAGENT_COHORTS:
        return SubagentExecutionEligibility(False, "rollout_ineligible")
    return SubagentExecutionEligibility(True)
