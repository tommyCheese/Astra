"""Deterministic OpenAI-compatible server for local latency benchmarks."""

from __future__ import annotations

import argparse
import json
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar


class StreamingModelHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    response_body: ClassVar[str]
    chunk_chars: ClassVar[int]
    first_token_delay: ClassVar[float]
    inter_chunk_delay: ClassVar[float]

    def setup(self) -> None:
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def do_POST(self) -> None:
        content_length = int(self.headers.get("content-length", "0"))
        request_body = self.rfile.read(content_length) if content_length else b"{}"
        if self.path.rstrip("/") != "/v1/chat/completions":
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        response_body = self._response_for_request(request_body)
        time.sleep(self.first_token_delay)
        for index in range(0, len(response_body), self.chunk_chars):
            text = response_body[index : index + self.chunk_chars]
            event = {
                "choices": [{"delta": {"content": text}}],
            }
            self._write_chunk(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
            if self.inter_chunk_delay:
                time.sleep(self.inter_chunk_delay)
        usage = {
            "choices": [],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
            },
        }
        self._write_chunk(f"data: {json.dumps(usage)}\n\n".encode())
        self._write_chunk(b"data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _response_for_request(self, request_body: bytes) -> str:
        try:
            request = json.loads(request_body)
        except (TypeError, ValueError):
            return self.response_body
        messages = request.get("messages") if isinstance(request, dict) else None
        if not isinstance(messages, list):
            return self.response_body
        system = str(messages[0].get("content", "")) if messages and isinstance(messages[0], dict) else ""
        user = str(messages[-1].get("content", "")) if messages and isinstance(messages[-1], dict) else ""
        if "fast model-driven agent" in system:
            payload = {"protocol_version": 1, "action": "answer", "content": _SUMMARY}
        elif "Create an audit-safe task contract" in system:
            payload = {
                "original_goal": "",
                "deliverables": ["直接回答用户请求"],
                "success_criteria": [],
                "verification_requirements": [],
                "risk_level": "low",
                "ambiguity_status": "clear",
            }
        elif "You are the planner" in system:
            payload = {
                "nodes": [
                    {
                        "node_key": "answer",
                        "title": "生成回答",
                        "intent": "直接回答稳定知识问题",
                        "depends_on": [],
                        "required_capabilities": [],
                        "success_criteria_refs": [],
                        "expected_outcome": {
                            "kind": "final_answer",
                            "success_condition": "回答已生成",
                            "required_fields": ["summary"],
                        },
                        "risk_level": "low",
                        "optional": False,
                    }
                ]
            }
        elif "general Agent controller and answer engine" in system:
            if '"active_node":null' in user.replace(" ", ""):
                payload = _trusted_answer()
            else:
                payload = {
                    "decision_type": "complete_node",
                    "reasoning_summary": "已生成稳定知识回答。",
                    "node_result": {"summary": _SUMMARY},
                }
        elif "general Agent loop controller" in system:
            payload = {"decision_type": "finalize", "reasoning_summary": "可以直接回答。"}
        elif "general answer engine" in system:
            payload = _final_answer()
        else:
            return self.response_body
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _write_chunk(self, payload: bytes) -> None:
        self.wfile.write(f"{len(payload):X}\r\n".encode())
        self.wfile.write(payload)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def log_message(self, format: str, *args: object) -> None:
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a deterministic OpenAI-compatible streaming model for latency benchmarks."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8999)
    parser.add_argument("--chunk-chars", type=int, default=3)
    parser.add_argument("--first-token-delay-ms", type=float, default=20)
    parser.add_argument("--inter-chunk-delay-ms", type=float, default=1)
    parser.add_argument(
        "--response-order",
        choices=("reasoning-first", "summary-first", "legacy"),
        default="reasoning-first",
    )
    return parser


_SUMMARY = (
    "递归是函数调用自身来逐步缩小问题。设置终止条件后，每次调用都更接近它。例如 factorial(n) 可返回 n * factorial(n - 1)。"
)


def _final_answer() -> dict[str, object]:
    return {
        "summary": _SUMMARY,
        "findings": [],
        "claims": [],
        "citations": [],
        "sources": [],
        "failed_sources": [],
        "source_quality": [],
        "conflicts": [],
        "caveats": [],
        "verification_notes": [],
    }


def _trusted_answer() -> dict[str, object]:
    return {
        "reasoning_summary": "正在组织直接回答。",
        "final_answer": _final_answer(),
        "decision_type": "finalize",
    }


def main() -> None:
    args = build_parser().parse_args()
    if args.chunk_chars < 1:
        raise SystemExit("--chunk-chars must be positive")
    if args.response_order == "legacy":
        response = {
            "decision_type": "finalize",
            "reasoning_summary": "可以直接回答",
            "final_answer": {"summary": _SUMMARY},
        }
    elif args.response_order == "summary-first":
        response = {
            "summary": _SUMMARY,
            "decision_type": "finalize",
            "reasoning_summary": "可以直接回答",
        }
    else:
        response = {
            "reasoning_summary": "正在组织直接回答",
            "summary": _SUMMARY,
            "decision_type": "finalize",
        }
    StreamingModelHandler.response_body = json.dumps(
        response,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    StreamingModelHandler.chunk_chars = args.chunk_chars
    StreamingModelHandler.first_token_delay = args.first_token_delay_ms / 1000
    StreamingModelHandler.inter_chunk_delay = args.inter_chunk_delay_ms / 1000
    server = ThreadingHTTPServer((args.host, args.port), StreamingModelHandler)
    print(f"model stub listening on http://{args.host}:{args.port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
