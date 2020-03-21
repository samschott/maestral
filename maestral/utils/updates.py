# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
# system imports
import json
import ssl
from packaging.version import Version
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

# local imports
from maestral import __version__


API_URL = 'https://api.github.com/repos/samschott/maestral-dropbox/releases'


def get_newer_version(version, releases):
    """
    Checks current version against a version list of releases to see if an update is
    available.

    :param str version: The current version.
    :param iterable[str] releases: A list of valid cleaned releases.
    :returns: The version string of the latest release if a newer release is available.
    :rtype: str
    """

    # filter releases, only offer updates to stable versions
    releases = [r for r in releases if is_stable_version(r)]
    releases.sort(key=lambda x: Version(x))
    latest_release = releases[-1]

    return latest_release if Version(version) < Version(latest_release) else None


def check_update_available(current_version=__version__):
    """
    Main method to check for updates.

    :param str current_version: The current version.
    :returns: A dictionary containing information about the latest release or an error
        message if retrieving update information failed.
    :rtype: dict
    """
    current_version = current_version.strip('v')
    new_version = None
    update_release_notes = ''
    error_msg = None

    try:
        if hasattr(ssl, '_create_unverified_context'):
            context = ssl._create_unverified_context()
            page = urlopen(API_URL, context=context)
        else:
            page = urlopen(API_URL)
        try:
            data = page.read()

            if not isinstance(data, str):
                data = data.decode()
            data = json.loads(data)

            releases = [item['tag_name'].replace('v', '') for item in data]
            release_notes = ['### ' + item['tag_name'] + '\n\n' + item['body']
                             for item in data]

            try:
                current_release_index = releases.index(current_version)
            except ValueError:
                # if current release cannot be found online, just
                # show release notes from newest release w/o history
                current_release_index = 1

            update_release_notes = release_notes[0:current_release_index]
            update_release_notes = '\n'.join(update_release_notes)

            new_version = get_newer_version(current_version, releases)
        except Exception:
            error_msg = 'Unable to retrieve information.'
    except HTTPError:
        error_msg = 'Unable to retrieve information.'
    except URLError:
        error_msg = ('Unable to connect to the internet. '
                     'Please make sure the connection is working properly.')
    except Exception:
        error_msg = 'Unable to check for updates.'

    return {'update_available': bool(new_version),
            'latest_release': new_version or current_version,
            'release_notes': update_release_notes,
            'error': error_msg}


def is_stable_version(version):
    """
    Return true if version is stable.

    Stable version examples: ``0.1.0``, ``1.2``, ``1.3.4``, ``1.0.5.post1``.
    Non-stable version examples: ``1.3.4.beta``, ``0.1.0-rc1``, ``3.0.0dev0``.
    """
    return not Version(version).is_prerelease
