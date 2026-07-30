from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import ValidationError as PydanticValidationError

from app.schemas.schedules import ActiveHours, ScheduleSpec

DURATION_RE = re.compile(r"^(?P<value>[1-9][0-9]*)(?P<unit>[smhdw])$")
ACTIVE_HOURS_RE = re.compile(
    r"^(?P<start>[0-9]{1,2}:[0-9]{2})-(?P<end>[0-9]{1,2}:[0-9]{2})$"
)


class CommandUsageError(ValueError):
    def __init__(self, message: str, *, usage: str):
        super().__init__(message)
        self.usage = usage


@dataclass(frozen=True)
class ScheduleCommand:
    action: Literal["list", "show", "create", "pause", "resume", "run", "delete"]
    job_id: str | None = None
    version: int | None = None
    idempotency_key: str | None = None
    schedule: ScheduleSpec | None = None
    timezone: str = "UTC"
    name: str | None = None
    prompt: str | None = None


@dataclass(frozen=True)
class HeartbeatCommand:
    action: Literal["status", "on", "off", "run"]
    interval_seconds: int | None = None
    timezone: str | None = None
    active_hours: ActiveHours | None = None
    prompt: str | None = None
    idempotency_key: str | None = None


def tokenize_arguments(arguments: str, *, usage: str) -> list[str]:
    try:
        return shlex.split(arguments, posix=True)
    except ValueError as exc:
        raise CommandUsageError("命令参数中的引号没有闭合。", usage=usage) from exc


def parse_duration(value: str, *, usage: str) -> int:
    match = DURATION_RE.fullmatch(value.strip().lower())
    if match is None:
        raise CommandUsageError(
            "周期必须使用 30m、2h、1d 等格式。",
            usage=usage,
        )
    multipliers = {"s": 1, "m": 60, "h": 3_600, "d": 86_400, "w": 604_800}
    seconds = int(match.group("value")) * multipliers[match.group("unit")]
    if not 60 <= seconds <= 31_536_000:
        raise CommandUsageError("周期必须在 60 秒到 365 天之间。", usage=usage)
    return seconds


def parse_schedule_command(arguments: str) -> ScheduleCommand:
    usage = (
        "/schedule list | show <id> | create "
        "(--every <30m>|--cron '<expr>'|--at <RFC3339>) "
        "[--tz <IANA>] [--name <name>] <prompt> | "
        "pause|resume|delete <id> --version <n> | "
        "run <id> [--idempotency-key <key>]"
    )
    tokens = tokenize_arguments(arguments, usage=usage)
    if not tokens:
        raise CommandUsageError("缺少 schedule 子命令。", usage=usage)
    action = tokens.pop(0)
    allowed_actions = {"list", "show", "create", "pause", "resume", "run", "delete"}
    if action not in allowed_actions:
        raise CommandUsageError(f"未知 schedule 子命令：{action}", usage=usage)

    flags, positionals = _parse_flags(
        tokens,
        allowed={
            "--every",
            "--cron",
            "--at",
            "--tz",
            "--name",
            "--version",
            "--idempotency-key",
        },
        usage=usage,
    )
    if action == "list":
        _require_no_parameters(flags, positionals, usage)
        return ScheduleCommand(action="list")
    if action == "show":
        _require_flags(flags, allowed=set(), usage=usage)
        return ScheduleCommand(action="show", job_id=_single_positional(positionals, usage))
    if action == "create":
        _require_flags(
            flags,
            allowed={"--every", "--cron", "--at", "--tz", "--name"},
            usage=usage,
        )
        schedule_flags = [
            flag for flag in ("--every", "--cron", "--at") if flag in flags
        ]
        if len(schedule_flags) != 1:
            raise CommandUsageError(
                "create 必须且只能指定 --every、--cron 或 --at 之一。",
                usage=usage,
            )
        schedule_flag = schedule_flags[0]
        if schedule_flag == "--every":
            schedule = ScheduleSpec(
                type="interval",
                interval_seconds=parse_duration(flags[schedule_flag], usage=usage),
            )
        elif schedule_flag == "--cron":
            try:
                schedule = ScheduleSpec(
                    type="cron", expression=flags[schedule_flag]
                )
            except PydanticValidationError as exc:
                raise CommandUsageError(
                    "--cron 必须是有效的标准五字段表达式。",
                    usage=usage,
                ) from exc
        else:
            try:
                at = datetime.fromisoformat(flags[schedule_flag].replace("Z", "+00:00"))
                schedule = ScheduleSpec(type="once", at=at)
            except ValueError as exc:
                raise CommandUsageError(
                    "--at 必须是带时区的 RFC 3339 时间。",
                    usage=usage,
                ) from exc
        prompt = " ".join(positionals).strip()
        if not prompt:
            raise CommandUsageError("create 必须提供任务 prompt。", usage=usage)
        return ScheduleCommand(
            action="create",
            schedule=schedule,
            timezone=flags.get("--tz", "UTC"),
            name=flags.get("--name"),
            prompt=prompt,
        )

    job_id = _single_positional(positionals, usage)
    if action in {"pause", "resume", "delete"}:
        _require_flags(flags, allowed={"--version"}, usage=usage)
        raw_version = flags.get("--version")
        if raw_version is None or not raw_version.isdigit() or int(raw_version) < 1:
            raise CommandUsageError(
                f"{action} 必须提供正整数 --version。",
                usage=usage,
            )
        return ScheduleCommand(
            action=action,  # type: ignore[arg-type]
            job_id=job_id,
            version=int(raw_version),
        )

    _require_flags(flags, allowed={"--idempotency-key"}, usage=usage)
    _validate_idempotency_key(flags.get("--idempotency-key"), usage)
    return ScheduleCommand(
        action="run",
        job_id=job_id,
        idempotency_key=flags.get("--idempotency-key"),
    )


def parse_heartbeat_command(arguments: str) -> HeartbeatCommand:
    usage = (
        "/heartbeat status | on --every <30m> [--tz <IANA>] "
        "[--active <09:00-22:00>] [prompt] | off | "
        "run [--idempotency-key <key>]"
    )
    tokens = tokenize_arguments(arguments, usage=usage)
    if not tokens:
        raise CommandUsageError("缺少 heartbeat 子命令。", usage=usage)
    action = tokens.pop(0)
    if action not in {"status", "on", "off", "run"}:
        raise CommandUsageError(f"未知 heartbeat 子命令：{action}", usage=usage)
    flags, positionals = _parse_flags(
        tokens,
        allowed={"--every", "--tz", "--active", "--idempotency-key"},
        usage=usage,
    )
    if action in {"status", "off"}:
        _require_no_parameters(flags, positionals, usage)
        return HeartbeatCommand(action=action)  # type: ignore[arg-type]
    if action == "run":
        _require_flags(flags, allowed={"--idempotency-key"}, usage=usage)
        if positionals:
            raise CommandUsageError("run 不接受额外位置参数。", usage=usage)
        idempotency_key = flags.get("--idempotency-key")
        _validate_idempotency_key(idempotency_key, usage)
        return HeartbeatCommand(
            action="run",
            idempotency_key=idempotency_key,
        )

    _require_flags(
        flags,
        allowed={"--every", "--tz", "--active"},
        usage=usage,
    )
    if "--every" not in flags:
        raise CommandUsageError("heartbeat on 必须提供 --every。", usage=usage)
    active_hours = None
    if "--active" in flags:
        match = ACTIVE_HOURS_RE.fullmatch(flags["--active"])
        if match is None:
            raise CommandUsageError(
                "--active 必须使用 09:00-22:00 格式。",
                usage=usage,
            )
        try:
            active_hours = ActiveHours(
                start=match.group("start"),
                end=match.group("end"),
            )
        except PydanticValidationError as exc:
            raise CommandUsageError(
                "--active 必须包含有效的 24 小时时间。",
                usage=usage,
            ) from exc
    prompt = " ".join(positionals).strip() or None
    return HeartbeatCommand(
        action="on",
        interval_seconds=parse_duration(flags["--every"], usage=usage),
        timezone=flags.get("--tz"),
        active_hours=active_hours,
        prompt=prompt,
    )


def _parse_flags(
    tokens: list[str],
    *,
    allowed: set[str],
    usage: str,
) -> tuple[dict[str, str], list[str]]:
    flags: dict[str, str] = {}
    positionals: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("--"):
            positionals.append(token)
            index += 1
            continue
        if token not in allowed:
            raise CommandUsageError(f"未知参数：{token}", usage=usage)
        if token in flags:
            raise CommandUsageError(f"参数不能重复：{token}", usage=usage)
        if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
            raise CommandUsageError(f"参数缺少值：{token}", usage=usage)
        flags[token] = tokens[index + 1]
        index += 2
    return flags, positionals


def _require_flags(
    flags: dict[str, str],
    *,
    allowed: set[str],
    usage: str,
) -> None:
    unexpected = set(flags) - allowed
    if unexpected:
        raise CommandUsageError(
            f"此子命令不接受参数：{sorted(unexpected)[0]}",
            usage=usage,
        )


def _require_no_parameters(
    flags: dict[str, str],
    positionals: list[str],
    usage: str,
) -> None:
    if flags or positionals:
        raise CommandUsageError("此子命令不接受额外参数。", usage=usage)


def _single_positional(positionals: list[str], usage: str) -> str:
    if len(positionals) != 1:
        raise CommandUsageError("此子命令需要且只能提供一个任务 id。", usage=usage)
    return positionals[0]


def _validate_idempotency_key(value: str | None, usage: str) -> None:
    if value is not None and len(value) > 240:
        raise CommandUsageError(
            "--idempotency-key 最多允许 240 个字符。",
            usage=usage,
        )
