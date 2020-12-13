# -*- coding: utf-8 -*-

import logging

import pytest
from click.testing import CliRunner

from maestral.cli import main
from maestral.main import Maestral
from maestral.config import remove_configuration
from maestral.autostart import AutoStart
from maestral.notify import MaestralDesktopNotifier


@pytest.fixture
def m():
    yield Maestral("test-config")
    remove_configuration("test-config")


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


def test_file_status(m):
    runner = CliRunner()
    result = runner.invoke(main, ["file-status", "/usr", "-c", "test-config"])

    assert result.exit_code == 0
    assert result.output == "unwatched\n"

    result = runner.invoke(main, ["file-status", "/invalid-dir", "-c", "test-config"])

    # the exception will be already raised by click's argument check
    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert "'/invalid-dir' does not exist" in result.output


def test_history(m):
    runner = CliRunner()
    result = runner.invoke(main, ["history", "-c", "test-config"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "No Dropbox account linked." in result.output


def test_ls(m):
    runner = CliRunner()
    result = runner.invoke(main, ["ls", "/", "-c", "test-config"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "No Dropbox account linked." in result.output


def test_autostart(m):
    autostart = AutoStart(m.config_name)
    autostart.disable()

    runner = CliRunner()
    result = runner.invoke(main, ["autostart", "-c", "test-config"])

    assert result.exit_code == 0
    assert "disabled" in result.output

    result = runner.invoke(main, ["autostart", "-Y", "-c", "test-config"])

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

    result = runner.invoke(main, ["autostart", "-N", "-c", "test-config"])

    assert result.exit_code == 0
    assert "Disabled" in result.output
    assert not autostart.enabled


def test_excluded_list(m):
    runner = CliRunner()
    result = runner.invoke(main, ["excluded", "list", "-c", "test-config"])

    assert result.exit_code == 0
    assert result.output == "No excluded files or folders.\n"


def test_excluded_add(m):
    runner = CliRunner()
    result = runner.invoke(main, ["excluded", "add", "/test", "-c", "test-config"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "No Dropbox account linked." in result.output


def test_excluded_remove(m):
    runner = CliRunner()
    result = runner.invoke(main, ["excluded", "remove", "/test", "-c", "test-config"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Daemon must be running to download folders." in result.output


def test_notify_level(m):
    runner = CliRunner()
    result = runner.invoke(main, ["notify", "level", "-c", "test-config"])

    level_name = MaestralDesktopNotifier.level_number_to_name(m.notification_level)

    assert result.exit_code == 0
    assert level_name in result.output

    result = runner.invoke(main, ["notify", "level", "SYNCISSUE", "-c", "test-config"])
    assert result.exit_code == 0
    assert "SYNCISSUE" in result.output

    result = runner.invoke(main, ["notify", "level", "INVALID", "-c", "test-config"])
    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)


def test_log_level(m):
    runner = CliRunner()
    result = runner.invoke(main, ["log", "level", "-c", "test-config"])

    level_name = logging.getLevelName(m.log_level)

    assert result.exit_code == 0
    assert level_name in result.output

    result = runner.invoke(main, ["log", "level", "DEBUG", "-c", "test-config"])
    assert result.exit_code == 0
    assert "DEBUG" in result.output

    result = runner.invoke(main, ["notify", "level", "INVALID", "-c", "test-config"])
    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
