# -*- coding: utf-8 -*-

import logging

from click.testing import CliRunner

from maestral.cli import main
from maestral.main import logger
from maestral.autostart import AutoStart
from maestral.notify import level_number_to_name, level_name_to_number
from maestral.daemon import MaestralProxy, start_maestral_daemon_process, Start


def test_help():
    runner = CliRunner()
    result = runner.invoke(main)

    assert result.exit_code == 0
    assert result.output.startswith("Usage: main [OPTIONS] COMMAND [ARGS]")


def test_invalid_config(m):
    runner = CliRunner()
    result = runner.invoke(main, ["resume", "-c", "non-existent-config"])

    assert result.exit_code == 1
    assert (
        result.output == "! Configuration 'non-existent-config' does not exist. "
        "Use 'maestral configs' to list all configurations.\n"
    )


def test_start(config_name):

    res = start_maestral_daemon_process(config_name, timeout=20)
    assert res is Start.Ok

    runner = CliRunner()
    result = runner.invoke(main, ["start", "-c", config_name])

    assert result.exit_code == 0
    assert "already running" in result.output


def test_stop(config_name):

    res = start_maestral_daemon_process(config_name, timeout=20)
    assert res is Start.Ok

    runner = CliRunner()
    result = runner.invoke(main, ["stop", "-c", config_name])

    assert result.exit_code == 0


def test_file_status(m):
    runner = CliRunner()
    result = runner.invoke(main, ["file-status", "/usr", "-c", m.config_name])

    assert result.exit_code == 0
    assert result.output == "unwatched\n"

    result = runner.invoke(main, ["file-status", "/invalid-dir", "-c", m.config_name])

    # the exception will be already raised by click's argument check
    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert "'/invalid-dir' does not exist" in result.output


def test_history(m):
    runner = CliRunner()
    result = runner.invoke(main, ["history", "-c", m.config_name])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "No Dropbox account linked." in result.output


def test_ls(m):
    runner = CliRunner()
    result = runner.invoke(main, ["ls", "/", "-c", m.config_name])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "No Dropbox account linked." in result.output


def test_autostart(m):
    autostart = AutoStart(m.config_name)
    autostart.disable()

    runner = CliRunner()
    result = runner.invoke(main, ["autostart", "-c", m.config_name])

    assert result.exit_code == 0
    assert "disabled" in result.output

    result = runner.invoke(main, ["autostart", "-Y", "-c", m.config_name])

    if autostart.implementation:
        if result.exit_code == 0:
            assert "Enabled" in result.output
            assert autostart.enabled
        else:
            # TODO: be more specific here
            assert result.exception is not None
    else:
        assert "not supported" in result.output
        assert not autostart.enabled

    result = runner.invoke(main, ["autostart", "-N", "-c", m.config_name])

    assert result.exit_code == 0
    assert "Disabled" in result.output
    assert not autostart.enabled


def test_excluded_list(m):
    runner = CliRunner()
    result = runner.invoke(main, ["excluded", "list", "-c", m.config_name])

    assert result.exit_code == 0
    assert result.output == "No excluded files or folders.\n"


def test_excluded_add(m):
    runner = CliRunner()
    result = runner.invoke(main, ["excluded", "add", "/test", "-c", m.config_name])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "No Dropbox account linked." in result.output


def test_excluded_remove(m):
    runner = CliRunner()
    result = runner.invoke(main, ["excluded", "remove", "/test", "-c", m.config_name])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Daemon must be running to download folders." in result.output


def test_notify_level(config_name):

    start_maestral_daemon_process(config_name, timeout=20)
    m = MaestralProxy(config_name)

    runner = CliRunner()
    result = runner.invoke(main, ["notify", "level", "-c", m.config_name])

    level_name = level_number_to_name(m.notification_level)

    assert result.exit_code == 0
    assert level_name in result.output

    level_name = "SYNCISSUE"
    level_number = level_name_to_number(level_name)
    result = runner.invoke(main, ["notify", "level", level_name, "-c", m.config_name])

    assert result.exit_code == 0
    assert level_name in result.output
    assert m.notification_level == level_number

    result = runner.invoke(main, ["notify", "level", "INVALID", "-c", m.config_name])

    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)


def test_notify_snooze(config_name):

    start_maestral_daemon_process(config_name, timeout=20)
    m = MaestralProxy(config_name)

    runner = CliRunner()
    result = runner.invoke(main, ["notify", "snooze", "20", "-c", m.config_name])

    assert result.exit_code == 0
    assert 0 < m.notification_snooze <= 20

    result = runner.invoke(main, ["notify", "snooze", "0", "-c", m.config_name])

    assert result.exit_code == 0
    assert m.notification_snooze == 0


def test_log_level(m):
    runner = CliRunner()
    result = runner.invoke(main, ["log", "level", "-c", m.config_name])

    level_name = logging.getLevelName(m.log_level)

    assert result.exit_code == 0
    assert level_name in result.output

    result = runner.invoke(main, ["log", "level", "DEBUG", "-c", m.config_name])
    assert result.exit_code == 0
    assert "DEBUG" in result.output

    result = runner.invoke(main, ["notify", "level", "INVALID", "-c", m.config_name])
    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)


def test_log_show(m):
    # log a message
    logger.info("Hello from pytest!")
    runner = CliRunner()
    result = runner.invoke(main, ["log", "show", "-c", m.config_name])

    assert result.exit_code == 0
    assert "Hello from pytest!" in result.output


def test_log_clear(m):
    # log a message
    logger.info("Hello from pytest!")
    runner = CliRunner()
    result = runner.invoke(main, ["log", "show", "-c", m.config_name])

    assert result.exit_code == 0
    assert "Hello from pytest!" in result.output

    # clear the logs
    result = runner.invoke(main, ["log", "clear", "-c", m.config_name])
    assert result.exit_code == 0

    with open(m.log_handler_file.stream.name) as f:
        log_content = f.read()

    assert log_content == ""
