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
        if content_length:
            self.rfile.read(content_length)
        if self.path.rstrip("/") != "/v1/chat/completions":
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        time.sleep(self.first_token_delay)
        for index in range(0, len(self.response_body), self.chunk_chars):
            text = self.response_body[index : index + self.chunk_chars]
            event = {
                "choices": [{"delta": {"content": text}}],
            }
            self._write_chunk(
                f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
            )
            if self.inter_chunk_delay:
                time.sleep(self.inter_chunk_delay)
        self._write_chunk(b"data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

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


def main() -> None:
    args = build_parser().parse_args()
    if args.chunk_chars < 1:
        raise SystemExit("--chunk-chars must be positive")
    summary = (
        "递归是函数调用自身来逐步缩小问题。设置终止条件后，每次调用都更接近它。"
        "例如 factorial(n) 可返回 n * factorial(n - 1)。"
    )
    if args.response_order == "legacy":
        response = {
            "decision_type": "finalize",
            "reasoning_summary": "可以直接回答",
            "final_answer": {"summary": summary},
        }
    elif args.response_order == "summary-first":
        response = {
            "summary": summary,
            "decision_type": "finalize",
            "reasoning_summary": "可以直接回答",
        }
    else:
        response = {
            "reasoning_summary": "正在组织直接回答",
            "summary": summary,
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
