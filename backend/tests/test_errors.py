import httpx

from app.core.errors import run_error_from_exception


def test_model_connection_timeout_is_classified_as_dependency_error():
    request = httpx.Request("POST", "https://api.example.test/chat/completions")
    payload = run_error_from_exception(httpx.ConnectTimeout("timed out", request=request))

    assert payload["type"] == "dependency.model_unavailable"
    assert payload["code"] == "MODEL_ENDPOINT_UNAVAILABLE"
    assert payload["retryable"] is True
    assert payload["details"] == {"reason": "ConnectTimeout"}
