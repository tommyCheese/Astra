#!/usr/bin/env python3
import asyncio
import contextlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.tools.base import ToolExecutionError
from app.tools.web import WebFetchTool, WebSearchTool

REQUEST_PATH = Path("/input/request.json")
CONFIG_PATH = Path("/input/runtime-config.json")
MAX_REQUEST_BYTES = 256 * 1024
TOOLS = {"web_search": WebSearchTool, "web_fetch": WebFetchTool}
CREDENTIAL_KEYS = {
    "WEB_SEARCH_API_KEY",
    "GOOGLE_SEARCH_API_KEY",
    "GOOGLE_SEARCH_ENGINE_ID",
}


def config_int(config: dict[str, Any], name: str, default: int) -> int:
    try:
        return int(config.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class WebRuntimeSettings:
    allow_network_read: bool = True
    web_search_provider: str = "auto"
    web_search_api_key: str = ""
    google_search_api_key: str = ""
    google_search_engine_id: str = ""
    google_search_result_count: int = 5
    google_search_language: str = "lang_zh-CN"
    google_search_region: str = ""
    google_search_safe: str = "active"
    crawler_max_content_chars: int = 12000
    crawler_max_response_bytes: int = 2 * 1024 * 1024
    crawler_min_quality_chars: int = 240
    crawler_allow_proxy_fake_ip: bool = False

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "WebRuntimeSettings":
        return cls(
            allow_network_read=str(config.get("ALLOW_NETWORK_READ", "true")).lower()
            in {"1", "true", "yes"},
            web_search_provider=str(config.get("WEB_SEARCH_PROVIDER", "auto")),
            web_search_api_key=str(config.get("WEB_SEARCH_API_KEY", "")),
            google_search_api_key=str(config.get("GOOGLE_SEARCH_API_KEY", "")),
            google_search_engine_id=str(config.get("GOOGLE_SEARCH_ENGINE_ID", "")),
            google_search_result_count=config_int(
                config, "GOOGLE_SEARCH_RESULT_COUNT", 5
            ),
            google_search_language=str(
                config.get("GOOGLE_SEARCH_LANGUAGE", "lang_zh-CN")
            ),
            google_search_region=str(config.get("GOOGLE_SEARCH_REGION", "")),
            google_search_safe=str(config.get("GOOGLE_SEARCH_SAFE", "active")),
            crawler_max_content_chars=config_int(
                config, "CRAWLER_MAX_CONTENT_CHARS", 12000
            ),
            crawler_max_response_bytes=config_int(
                config, "CRAWLER_MAX_RESPONSE_BYTES", 2 * 1024 * 1024
            ),
            crawler_min_quality_chars=config_int(
                config, "CRAWLER_MIN_QUALITY_CHARS", 240
            ),
            crawler_allow_proxy_fake_ip=str(
                config.get("CRAWLER_ALLOW_PROXY_FAKE_IP", "false")
            ).lower()
            in {"1", "true", "yes"},
        )


def read_runtime_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Invalid runtime config")
    return config


def safe_message(value: Any) -> str:
    message = str(value)[:1000]
    try:
        config = read_runtime_config()
    except (json.JSONDecodeError, OSError, ValueError):
        config = {}
    for key in CREDENTIAL_KEYS:
        secret = str(config.get(key, ""))
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message


async def invoke() -> dict[str, Any]:
    raw = REQUEST_PATH.read_bytes()
    if len(raw) > MAX_REQUEST_BYTES:
        raise ToolExecutionError("invalid_input", "Sandbox tool request is too large")
    request = json.loads(raw)
    if not isinstance(request, dict) or request.get("version") != "1":
        raise ToolExecutionError("invalid_input", "Unsupported sandbox tool protocol")
    tool_name = request.get("tool")
    tool_input = request.get("input")
    if tool_name not in TOOLS or not isinstance(tool_input, dict):
        raise ToolExecutionError("invalid_input", "Invalid sandbox tool request")
    settings = WebRuntimeSettings.from_config(read_runtime_config())
    tool = TOOLS[tool_name](settings)
    with contextlib.redirect_stdout(sys.stderr):
        return await tool.run(tool_input)


def main() -> None:
    try:
        output = asyncio.run(invoke())
        envelope = {"ok": True, "output": output}
    except ToolExecutionError as exc:
        envelope = {
            "ok": False,
            "error": {"category": exc.category, "message": safe_message(exc.message)},
        }
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        envelope = {
            "ok": False,
            "error": {
                "category": "invalid_input",
                "message": "Invalid sandbox tool request",
            },
        }
    except Exception:
        envelope = {
            "ok": False,
            "error": {"category": "tool_failed", "message": "Sandboxed tool failed"},
        }
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
