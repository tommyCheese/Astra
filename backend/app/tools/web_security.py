from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import ParseResult, urlparse

import httpx
from charset_normalizer import from_bytes

from app.tools.base import ToolExecutionError

PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def validate_public_http_url(url: str) -> None:
    """Perform strict structural validation before resolving or requesting a URL."""
    if any(ord(character) < 32 or character.isspace() for character in url):
        raise ToolExecutionError("invalid_input", "URL contains whitespace or control characters")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolExecutionError("invalid_input", "web_fetch only supports HTTP(S) URLs")
    if parsed.username or parsed.password:
        raise ToolExecutionError("permission_denied", "URLs containing credentials are not allowed")
    hostname = parsed.hostname.rstrip(".").lower()
    _validate_hostname(hostname)
    _validate_standard_port(parsed)
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    validate_public_ip(address)


def _validate_hostname(hostname: str) -> None:
    if len(hostname) > 253:
        raise ToolExecutionError("invalid_input", "URL hostname is too long")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ToolExecutionError("permission_denied", "Local network targets are not allowed")


def _validate_standard_port(parsed: ParseResult) -> None:
    try:
        port = parsed.port
    except ValueError as exc:
        raise ToolExecutionError("invalid_input", "URL contains an invalid port") from exc
    expected_port = 80 if parsed.scheme == "http" else 443
    if port is not None and port != expected_port:
        raise ToolExecutionError(
            "permission_denied", "Only standard HTTP and HTTPS ports are allowed"
        )


def validate_public_ip(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_proxy_fake_ip: bool = False,
) -> None:
    if allow_proxy_fake_ip and address in PROXY_FAKE_IP_NETWORK:
        return
    if not address.is_global:
        raise ToolExecutionError(
            "permission_denied", "Private or reserved network targets are not allowed"
        )


async def validate_public_http_target(
    url: str,
    *,
    allow_proxy_fake_ip: bool = False,
) -> set[str]:
    """Resolve every A/AAAA target and reject the hop if any address is non-public."""
    validate_public_http_url(url)
    parsed = urlparse(url)
    if parsed.hostname is None:
        raise ToolExecutionError("invalid_input", "URL must include a hostname")
    hostname = parsed.hostname.rstrip(".").lower()
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        validate_public_ip(literal)
        return {str(literal)}

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        records = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise ToolExecutionError(
            "fetch_failed", f"Unable to resolve URL hostname: {hostname}"
        ) from exc
    addresses = {record[4][0].split("%", 1)[0] for record in records}
    if not addresses:
        raise ToolExecutionError("fetch_failed", f"URL hostname has no address records: {hostname}")
    for value in addresses:
        validate_public_ip(
            ipaddress.ip_address(value),
            allow_proxy_fake_ip=allow_proxy_fake_ip,
        )
    return addresses


def validate_fetch_content_type(content_type: str) -> None:
    media_type = content_type.split(";", 1)[0].strip().lower()
    allowed_application_types = {
        "application/atom+xml",
        "application/json",
        "application/rss+xml",
        "application/xhtml+xml",
        "application/xml",
    }
    if (
        media_type
        and not media_type.startswith("text/")
        and media_type not in allowed_application_types
    ):
        raise ToolExecutionError(
            "unsupported_content_type", f"Unsupported response content type: {media_type}"
        )


def validate_content_length(content_length: str | None, byte_limit: int) -> None:
    if not content_length:
        return
    try:
        declared_size = int(content_length)
    except ValueError:
        return
    if declared_size > byte_limit:
        raise ToolExecutionError(
            "response_too_large", f"Response exceeds the {byte_limit} byte limit"
        )


async def read_limited_body(response: httpx.Response, byte_limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > byte_limit:
            raise ToolExecutionError(
                "response_too_large", f"Response exceeds the {byte_limit} byte limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def decode_response_body(body: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset\s*=\s*[\"']?([^;\"']+)", content_type, re.I)
    if charset_match:
        try:
            return body.decode(charset_match.group(1).strip())
        except (LookupError, UnicodeDecodeError):
            pass
    detected = from_bytes(body).best()
    if detected is not None:
        return str(detected)
    return body.decode("utf-8", errors="replace")
