from app.repositories.approval_store import ApprovalStore
from app.repositories.tool_call_store import ToolCallStore


class RunToolApprovalStore(ToolCallStore, ApprovalStore):
    pass
