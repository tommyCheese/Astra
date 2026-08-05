import httpx
import pytest

from app.common.core.errors import run_error_from_exception
from app.infrastructure.tools.base import ToolExecutionError


def test_model_connection_timeout_is_classified_as_dependency_error():
    request = httpx.Request("POST", "https://api.example.test/chat/completions")
    payload = run_error_from_exception(httpx.ConnectTimeout("timed out", request=request))

    assert payload["type"] == "dependency.model_unavailable"
    assert payload["code"] == "MODEL_ENDPOINT_UNAVAILABLE"
    assert payload["retryable"] is True
    assert payload["details"] == {"reason": "ConnectTimeout"}


@pytest.mark.parametrize(
    ("category", "error_type", "code"),
    [
        ("provider_not_configured", "dependency.search_unavailable", "SEARCH_UNAVAILABLE"),
        ("extract_failed", "dependency.fetch_unavailable", "FETCH_UNAVAILABLE"),
        ("invalid_input", "validation.tool_input_invalid", "TOOL_INPUT_INVALID"),
        ("tool_not_allowed", "policy.tool_not_allowed", "TOOL_NOT_ALLOWED"),
        ("unexpected_error", "runtime.tool_failed", "TOOL_EXECUTION_FAILED"),
    ],
)
def test_tool_errors_have_stable_public_classification(category, error_type, code):
    payload = run_error_from_exception(ToolExecutionError(category, "sensitive provider detail"))

    assert payload["type"] == error_type
    assert payload["code"] == code
    assert "sensitive provider detail" not in payload["message"]
