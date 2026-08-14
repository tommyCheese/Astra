"""Capability declaration for Astra's supported AG-UI profile."""

from app.interfaces.ag_ui.compatibility import ACTIVITY_SCHEMA_VERSIONS, ASTRA_AG_UI_PROFILE_VERSION


def capability_document() -> dict[str, object]:
    return {
        "profileVersion": ASTRA_AG_UI_PROFILE_VERSION,
        "identity": {
            "name": "Astra",
            "type": "astra",
            "description": "Governed general-purpose Agent runtime",
            "version": ASTRA_AG_UI_PROFILE_VERSION,
        },
        "transport": {
            "streaming": True,
            "websocket": False,
            "httpBinary": False,
            "pushNotifications": False,
            "resumable": False,
        },
        "tools": {
            "supported": True,
            "parallelCalls": True,
            "clientProvided": False,
        },
        "state": {"snapshots": True, "deltas": False, "memory": True, "persistentState": True},
        "multiAgent": {"supported": True, "delegation": True, "handoffs": False},
        "reasoning": {"supported": True, "streaming": True, "encrypted": False},
        "humanInTheLoop": {
            "supported": True,
            "approvals": True,
            "interventions": True,
            "interrupts": True,
            "approveWithEdits": False,
        },
        "custom": {
            "astra": {
                "activitySchemas": ACTIVITY_SCHEMA_VERSIONS,
                "answerModes": ["standard", "trusted"],
                "crossConnectionDeltas": False,
                "activities": True,
                "cancellation": {
                    "supported": True,
                    "endpointTemplate": "/api/ag-ui/runs/{runId}/cancel?threadId={threadId}",
                },
            }
        },
    }
