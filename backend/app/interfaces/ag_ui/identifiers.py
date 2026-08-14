"""Deterministic public identifiers derived from durable Astra identities."""


def protocol_thread_id(thread_id: str) -> str:
    return thread_id


def protocol_run_id(run_id: str) -> str:
    return run_id


def answer_message_id(run_id: str) -> str:
    return f"astra-answer:{run_id}"


def reasoning_message_id(run_id: str, stream_id: str) -> str:
    return f"astra-reasoning:{run_id}:{stream_id}"


def tool_call_id(internal_tool_call_id: str) -> str:
    return f"astra-tool:{internal_tool_call_id}"


def plan_activity_id(plan_id: str) -> str:
    return f"astra-plan:{plan_id}"


def agent_tree_activity_id(run_id: str) -> str:
    return f"astra-agent-tree:{run_id}"


def activity_message_id(activity_type: str, entity_id: str) -> str:
    return f"astra-activity:{activity_type}:{entity_id}"


def interrupt_id(approval_id: str) -> str:
    return f"astra-interrupt:{approval_id}"


def waiting_interrupt_id(run_id: str, source_event_id: int) -> str:
    return f"astra-interrupt:{run_id}:waiting:{source_event_id}"
