# -*- coding: utf-8 -*-

import os

import pytest
from click.testing import CliRunner

from maestral.cli import main
from maestral.constants import IDLE, PAUSED, STOPPED, ERROR
from maestral.daemon import MaestralProxy


if not ("DROPBOX_ACCESS_TOKEN" in os.environ or "DROPBOX_REFRESH_TOKEN" in os.environ):
    pytest.skip("Requires auth token", allow_module_level=True)


def wait_for_idle(m: MaestralProxy, minimum: int = 2):

    while True:
        current_status = m.status
        if current_status in (IDLE, PAUSED, STOPPED, ERROR, ""):
            m.status_change_longpoll(timeout=minimum)
            if m.status == current_status:
                # status did not change, we are done
                return
        else:
            m.status_change_longpoll(timeout=minimum)


def test_pause_resume(proxy):

    runner = CliRunner()
    result = runner.invoke(main, ["pause", "-c", proxy.config_name])

    wait_for_idle(proxy)

    assert result.exit_code == 0
    assert proxy.paused

    result = runner.invoke(main, ["resume", "-c", proxy.config_name])

    wait_for_idle(proxy)

    assert result.exit_code == 0
    assert not proxy.paused


def test_status(proxy):
    runner = CliRunner()
    result = runner.invoke(main, ["status", "-c", proxy.config_name])

    assert result.exit_code == 0
    assert "Stopped" in result.output


def test_file_status(proxy):
    runner = CliRunner()
    proxy.start_sync()
    wait_for_idle(proxy)

    local_path = proxy.to_local_path("/sync_tests")

    result = runner.invoke(main, ["file-status", local_path, "-c", proxy.config_name])

    assert result.exit_code == 0
    assert result.output == "up to date\n"


def test_history(proxy):

    proxy.start_sync()
    wait_for_idle(proxy)

    # lets make history
    dbx_path = "/sync_tests/new_file.txt"
    local_path = proxy.to_local_path(dbx_path)

    with open(local_path, "a") as f:
        f.write("content")

    wait_for_idle(proxy)

    # check that history has been be written
    runner = CliRunner()
    result = runner.invoke(main, ["history", "-c", proxy.config_name])

    lines = result.output.strip().split("\n")

    assert result.exit_code == 0
    # last entry will be test.lock with change time in the future
    assert "/test.lock" in lines[-1]
    assert "added" in lines[-1]
    # then comes our own file
    assert dbx_path in lines[-2]
    assert "added" in lines[-2]


def test_ls(proxy):
    runner = CliRunner()
    result = runner.invoke(main, ["ls", "/", "-c", proxy.config_name])

    entries = proxy.list_folder("/")

    assert result.exit_code == 0

    for entry in entries:
        assert entry["name"] in result.output


def test_ls_long(proxy):
    runner = CliRunner()
    result = runner.invoke(main, ["ls", "-l", "/", "-c", proxy.config_name])

    lines = result.output.strip().split("\n")
    entries = proxy.list_folder("/")

    assert result.exit_code == 0
    assert lines[0].startswith("Loading...")  # loading indicator
    assert lines[1].startswith("Name")  # column titles

    for line, entry in zip(lines[2:], entries):
        assert entry["name"] in line
