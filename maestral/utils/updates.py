# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
# system imports
import json
import re
import ssl
from distutils.version import LooseVersion
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

# local imports
from maestral import __version__


API_URL = "https://api.github.com/repos/samschott/maestral-dropbox/releases"


def has_newer_version(version, releases):
    """Checks current version against a version list of releases to see if an update is
    available.

    :param str version: The current version.
    :param list[str] releases: A list of valid cleaned releases in chronological order,
        and the latest release.
    :returns: The version string of the latest release if a newer release is available.
    :rtype: str
    """

    # filter releases, only offer updates to stable versions
    releases = [r for r in releases if is_stable_version(r)]

    latest_release = releases[-1]

    return latest_release if check_version(version, latest_release, '<') else None


def check_update_available(current_version=__version__):
    """
    Main method to check for updates.

    :param str current_version: The current version.
    :returns: A dictionary containing information about the latest release or an error
        message if retrieving update information failed.
    :rtype: dict
    """
    current_version = current_version.strip("v")
    new_version = None
    release_notes = ""
    error_msg = None

    try:
        if hasattr(ssl, "_create_unverified_context"):
            context = ssl._create_unverified_context()
            page = urlopen(API_URL, context=context)
        else:
            page = urlopen(API_URL)
        try:
            data = page.read()

            if not isinstance(data, str):
                data = data.decode()
            data = json.loads(data)

            releases = [item["tag_name"].replace("v", "") for item in data]
            releases = list(reversed(releases))

            releases_notes = [item["body"] for item in data]
            releases_notes = list(reversed(releases_notes))
            release_notes = releases_notes[-1]

            new_version = has_newer_version(current_version, releases)
        except Exception:
            error_msg = "Unable to retrieve information."
    except HTTPError:
        error_msg = "Unable to retrieve information."
    except URLError:
        error_msg = ("Unable to connect to the internet. "
                     "Please make sure the connection is working properly.")
    except Exception:
        error_msg = "Unable to check for updates."

    return {"update_available": bool(new_version),
            "latest_release": new_version or current_version,
            "release_notes": release_notes,
            "error": error_msg}


def check_version(actver, version, cmp_op):
    """
    Check version string of an active module against a required version.

    If dev/prerelease tags result in TypeError for string-number comparison,
    it is assumed that the dependency is satisfied.
    Users on dev branches are responsible for keeping their own packages up to
    date.

    Copyright (C) 2013  The IPython Development Team

    Distributed under the terms of the BSD License.
    """
    if isinstance(actver, tuple):
        actver = '.'.join([str(i) for i in actver])

    # Hacks needed so that LooseVersion understands that (for example)
    # version = '3.0.0' is in fact bigger than actver = '3.0.0rc1'
    if (is_stable_version(version) and not is_stable_version(actver) and
            actver.startswith(version) and version != actver):
        version = version + 'zz'
    elif (is_stable_version(actver) and not is_stable_version(version) and
          version.startswith(actver) and version != actver):
        actver = actver + 'zz'

    try:
        if cmp_op == '>':
            return LooseVersion(actver) > LooseVersion(version)
        elif cmp_op == '>=':
            return LooseVersion(actver) >= LooseVersion(version)
        elif cmp_op == '=':
            return LooseVersion(actver) == LooseVersion(version)
        elif cmp_op == '<':
            return LooseVersion(actver) < LooseVersion(version)
        elif cmp_op == '<=':
            return LooseVersion(actver) <= LooseVersion(version)
        else:
            return False
    except TypeError:
        return True


def is_stable_version(version):
    """
    Return true if version is stable, i.e. with letters in the final component.

    Stable version examples: ``1.2``, ``1.3.4``, ``1.0.5``.
    Non-stable version examples: ``1.3.4beta``, ``0.1.0rc1``, ``3.0.0dev0``.
    """
    if not isinstance(version, tuple):
        version = version.split(".")
    last_part = version[-1]

    if not re.search(r"[a-zA-Z]", last_part):
        return True
    else:
        return False
