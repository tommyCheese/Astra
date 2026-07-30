import pytest

from app.system_command_parsing import (
    CommandUsageError,
    parse_heartbeat_command,
    parse_schedule_command,
)


def test_parse_schedule_create_interval_with_quoted_name():
    command = parse_schedule_command(
        'create --every 30m --tz Asia/Shanghai --name "晨间摘要" '
        '"总结昨夜明确记录的更新"'
    )

    assert command.action == "create"
    assert command.schedule is not None
    assert command.schedule.type.value == "interval"
    assert command.schedule.interval_seconds == 1_800
    assert command.timezone == "Asia/Shanghai"
    assert command.name == "晨间摘要"
    assert command.prompt == "总结昨夜明确记录的更新"


def test_parse_schedule_cron_requires_quoted_expression():
    command = parse_schedule_command(
        "create --cron '0 9 * * 1-5' --tz UTC 工作日报"
    )

    assert command.schedule is not None
    assert command.schedule.expression == "0 9 * * 1-5"


def test_parse_schedule_lifecycle_requires_version():
    with pytest.raises(CommandUsageError, match="--version"):
        parse_schedule_command("pause job-1")

    command = parse_schedule_command("resume job-1 --version 3")
    assert command.job_id == "job-1"
    assert command.version == 3


def test_parse_heartbeat_on_with_active_hours():
    command = parse_heartbeat_command(
        "on --every 30m --tz Asia/Shanghai --active 09:00-22:00 "
        '"只检查明确待办"'
    )

    assert command.action == "on"
    assert command.interval_seconds == 1_800
    assert command.active_hours is not None
    assert command.active_hours.start == "09:00"
    assert command.prompt == "只检查明确待办"


@pytest.mark.parametrize(
    ("parser", "arguments"),
    [
        (parse_schedule_command, "create --shell anything"),
        (parse_schedule_command, 'create --every 30m "unclosed'),
        (parse_schedule_command, "list extra"),
        (parse_schedule_command, "create --cron 'not a cron' work"),
        (
            parse_schedule_command,
            f"run job-1 --idempotency-key {'x' * 241}",
        ),
        (parse_heartbeat_command, "on --every 10s"),
        (parse_heartbeat_command, "on --every 30m --active 24:00-22:00"),
        (parse_heartbeat_command, "unknown"),
    ],
)
def test_command_parsers_fail_closed(parser, arguments):
    with pytest.raises(CommandUsageError):
        parser(arguments)
