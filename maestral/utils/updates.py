# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This module contains functions to check fr updates and retrieve change logs.

:const str API_URL: URL for the Github API.

"""

# system imports
import requests
from packaging.version import Version

# local imports
from maestral import __version__


CONNECTION_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ReadTimeout,
    requests.exceptions.RetryError,
    ConnectionError,
)

GITHUB_RELEAES_API = 'https://api.github.com/repos/samschott/maestral-dropbox/releases'


def get_newer_version(version, releases):
    """
    Checks current version against a version list of releases to see if an update is
    available. Only offers newer versions if they are not a prerelease.

    :param str version: The current version.
    :param iterable[str] releases: A list of valid cleaned releases.
    :returns: The version string of the latest release if a newer release is available.
    :rtype: str
    """

    releases = [r for r in releases if not Version(r).is_prerelease]
    releases.sort(key=lambda x: Version(x))
    latest_release = releases[-1]

    return latest_release if Version(version) < Version(latest_release) else None


def check_update_available(current_version=__version__):
    """
    Main method to check for updates.

    :param str current_version: The current version.
    :returns: A dictionary containing information about the latest stable release or an
        error message if retrieving update information failed. If available, release notes
        will be returned for all version from ``current_version`` to the latest stable
        release.
    :rtype: dict
    """
    current_version = current_version.lstrip('v')
    new_version = None
    update_release_notes = ''
    error_msg = None

    try:
        r = requests.get(GITHUB_RELEAES_API)
        data = r.json()

        releases = []
        release_notes = []

        # this should do nothing since the github API already returns sorted entries
        data.sort(key=lambda x: Version(x['tag_name']), reverse=True)

        for item in data:
            v = item['tag_name'].lstrip('v')
            if not Version(v).is_prerelease:
                releases.append(v)
                release_notes.append('### {tag_name}\n\n{body}'.format(**item))

        new_version = get_newer_version(current_version, releases)

        if new_version:

            # closest_release == current_version if current_version appears in the
            # release list. Otherwise closest_release < current_version
            closest_release = next(v for v in releases if Version(v) <= Version(current_version))
            closest_release_idx = releases.index(closest_release)

            update_release_notes_list = release_notes[0:closest_release_idx]
            update_release_notes = '\n'.join(update_release_notes_list)

    except requests.exceptions.HTTPError:
        error_msg = 'Unable to retrieve information. Please try again later.'
    except CONNECTION_ERRORS:
        error_msg = 'No internet connection. Please try again later.'
    except Exception:
        error_msg = 'Something when wrong. Please try again later.'

    return {'update_available': bool(new_version),
            'latest_release': new_version or current_version,
            'release_notes': update_release_notes,
            'error': error_msg}
