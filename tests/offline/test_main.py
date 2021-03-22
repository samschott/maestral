# -*- coding: utf-8 -*-

import requests
import maestral.main
from maestral.constants import GITHUB_RELEASES_API


def test_check_for_updates(m):

    # get current releases from GitHub

    resp = requests.get(GITHUB_RELEASES_API)

    try:
        resp.raise_for_status()
    except Exception:
        # rate limit etc, connection error, etc
        return

    data = resp.json()

    previous_release = data[1]["tag_name"].lstrip("v")
    latest_stable_release = data[0]["tag_name"].lstrip("v")

    # check that no update is offered from current (newest) version

    maestral.main.__version__ = latest_stable_release

    update_res = m.check_for_updates()

    assert update_res["latest_release"] == latest_stable_release
    assert not update_res["update_available"]
    assert update_res["release_notes"] == ""
    assert update_res["error"] is None

    # check that update is offered from previous release

    maestral.main.__version__ = previous_release

    update_res = m.check_for_updates()

    assert update_res["latest_release"] == latest_stable_release
    assert update_res["update_available"]
    assert update_res["release_notes"] != ""
    assert update_res["error"] is None
