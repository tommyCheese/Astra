from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import ClassVar

logger = logging.getLogger("astra.model")


class StreamingJsonFieldExtractor:
    """Incrementally decode selected JSON string fields in one pass."""

    _ESCAPES: ClassVar[dict[str, str]] = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }

    def __init__(self, fields: Iterable[str]) -> None:
        self._fields = frozenset(fields)
        self._completed: set[str] = set()
        self._in_string = False
        self._string_is_value = False
        self._string_chars: list[str] = []
        self._capture_field: str | None = None
        self._pending_key: str | None = None
        self._awaiting_value_key: str | None = None
        self._escaped = False
        self._unicode_digits: list[str] | None = None

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        captured: list[str] = []
        for char in chunk:
            if self._in_string:
                self._consume_string_char(char, events, captured)
            else:
                self._consume_structural_char(char)
        self._flush_capture(events, captured)
        return events

    def _consume_string_char(
        self,
        char: str,
        events: list[tuple[str, str]],
        captured: list[str],
    ) -> None:
        if self._unicode_digits is not None:
            self._consume_unicode_digit(char, captured)
        elif self._escaped:
            self._consume_escape(char, captured)
        elif char == "\\":
            self._escaped = True
        elif char == '"':
            self._finish_string(events, captured)
        else:
            self._append_decoded(char, captured)

    def _consume_unicode_digit(self, char: str, captured: list[str]) -> None:
        if char not in "0123456789abcdefABCDEF":
            return
        self._unicode_digits.append(char)
        if len(self._unicode_digits) == 4:
            self._append_decoded(chr(int("".join(self._unicode_digits), 16)), captured)
            self._unicode_digits = None
            self._escaped = False

    def _consume_escape(self, char: str, captured: list[str]) -> None:
        if char == "u":
            self._unicode_digits = []
        elif char in self._ESCAPES:
            self._append_decoded(self._ESCAPES[char], captured)
            self._escaped = False
        else:
            self._escaped = False

    def _append_decoded(self, value: str, captured: list[str]) -> None:
        if self._capture_field is not None:
            captured.append(value)
        else:
            self._string_chars.append(value)

    def _finish_string(
        self,
        events: list[tuple[str, str]],
        captured: list[str],
    ) -> None:
        self._flush_capture(events, captured)
        if self._capture_field is not None:
            events.append((self._capture_field, "\1"))
            self._completed.add(self._capture_field)
        elif not self._string_is_value:
            self._pending_key = "".join(self._string_chars)
        self._in_string = False
        self._capture_field = None
        self._string_chars.clear()

    def _consume_structural_char(self, char: str) -> None:
        if char.isspace():
            return
        if char == '"':
            self._start_string()
            return
        if char == ":" and self._pending_key is not None:
            self._awaiting_value_key = self._pending_key
            self._pending_key = None
            return
        self._pending_key = None
        self._awaiting_value_key = None

    def _start_string(self) -> None:
        key = self._awaiting_value_key
        self._awaiting_value_key = None
        self._in_string = True
        self._string_is_value = key is not None
        self._string_chars.clear()
        self._capture_field = key if key in self._fields and key not in self._completed else None
        self._escaped = False
        self._unicode_digits = None

    def _flush_capture(
        self,
        events: list[tuple[str, str]],
        captured: list[str],
    ) -> None:
        if self._capture_field is not None and captured:
            events.append((self._capture_field, "".join(captured)))
            captured.clear()


def extract_partial_json_string(content: str, field: str) -> str:
    """Return the safely decoded portion of a JSON string field before the object is complete."""
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"', content)
    if not match:
        return ""
    index = match.end()
    decoded: list[str] = []
    escapes = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    while index < len(content):
        char = content[index]
        if char == '"':
            break
        if char != "\\":
            decoded.append(char)
            index += 1
            continue
        if index + 1 >= len(content):
            break
        escaped = content[index + 1]
        if escaped == "u":
            if index + 6 > len(content):
                break
            codepoint = content[index + 2 : index + 6]
            if not re.fullmatch(r"[0-9a-fA-F]{4}", codepoint):
                break
            decoded.append(chr(int(codepoint, 16)))
            index += 6
            continue
        if escaped not in escapes:
            break
        decoded.append(escapes[escaped])
        index += 2
    return "".join(decoded)


def json_string_field_complete(content: str, field: str) -> bool:
    """Return whether a streamed JSON string field has received its closing quote."""
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"', content)
    if not match:
        return False
    escaped = False
    for char in content[match.end() :]:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return True
    return False
