# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This module contains functions for common path operations used by Maestral.

"""

# system imports
import os
import os.path as osp
import shutil
import tempfile


def is_fs_case_sensitive(path):
    # create a cased temp file and check if the lower case version exists
    with tempfile.NamedTemporaryFile(dir=path, prefix='.TmP') as tmp_file:
        return not os.path.exists(tmp_file.name.lower())


def is_child(path, parent):
    """
    Checks if ``path`` semantically is inside ``parent``. Neither path needs to
    refer to an actual item on the drive. This function is case sensitive.

    :param str path: Item path.
    :param str parent: Parent path.
    :returns: ``True`` if ``path`` semantically lies inside ``parent`` or
        ``path == parent``, ``False`` otherwise.
    :rtype: bool
    """

    parent = parent.rstrip(osp.sep) + osp.sep
    path = path.rstrip(osp.sep)

    return path.startswith(parent)


def is_equal_or_child(path, parent):
    """
    Checks if ``path`` semantically is inside ``parent`` or equals ``parent``. Neither
    path needs to refer to an actual item on the drive. This function is case sensitive.

    :param str path: Item path.
    :param str parent: Parent path.
    :returns: ``True`` if ``path`` semantically lies inside ``parent`` or
        ``path == parent``.
    :rtype: bool
    """

    return is_child(path, parent) or path == parent


def path_exists_case_insensitive(path, root='/'):
    """
    Checks if a ``path`` exists in given ``root`` directory, similar to ``os.path.exists``
    but case-insensitive. A list of all case-insensitive matches is returned.

    :param str path: Path relative to ``root``.
    :param str root: Directory where we will look for ``path``. There are significant
        performance improvements if a root directory with a small tree is given.
    :return: List of absolute and case-sensitive to search results.
    :rtype: list[str]
    """

    if not osp.isdir(root):
        return []

    if path in ('', '/'):
        return [root]

    path_list = path.lstrip(osp.sep).split(osp.sep)
    path_list_lower = [x.lower() for x in path_list]

    i = 0
    local_paths = []
    for root, dirs, files in os.walk(root):
        for d in list(dirs):
            if d.lower() != path_list_lower[i]:
                dirs.remove(d)
        for f in list(files):
            if f.lower() != path_list_lower[i]:
                files.remove(f)

        local_paths = [osp.join(root, name) for name in dirs + files]

        i += 1
        if i == len(path_list_lower):
            break

    return local_paths


def to_cased_path(path, root='/'):
    """
    Returns a cased version of the given path, if exists in the given root directory,
    or an empty string otherwise.

    :param str path: Original path.
    :param str root: Parent directory to search in.
    :returns: Absolute and cased version of given path or empty string.
    :rtype: str
    """

    path_list = path_exists_case_insensitive(path, root)

    if len(path_list) > 0:
        return path_list[0]
    else:
        return ''


def generate_cc_name(path, suffix='conflicting copy'):
    """
    Generates a path for a conflicting copy of ``path``. The file name is created by
    inserting the given ``suffix`` between the the filename and extension. For instance:

        'my_file.txt' -> 'my_file (conflicting copy).txt'

    If a file with the resulting path already exists (case-insensitive!), we additionally
    append an integer number, for instance:

        'my_file.txt' -> 'my_file (conflicting copy 1).txt'

    :param str path: Original path name.
    :param str suffix: Suffix to use. Defaults to 'conflicting copy'.
    :returns: New path.
    :rtype: str
    """

    dirname, basename = osp.split(path)
    filename, ext = osp.splitext(basename)

    i = 0
    cc_candidate = f'{filename} ({suffix}){ext}'

    while path_exists_case_insensitive(cc_candidate, dirname):
        i += 1
        cc_candidate = f'{filename} ({suffix} {i}){ext}'

    return osp.join(dirname, cc_candidate)


def delete(path, raise_error=False):
    """
    Deletes a file or folder at ``path``. Exceptions are either raised or returned if
    ``raise_error`` is False.
    """
    err = None

    try:
        shutil.rmtree(path)
    except OSError:
        try:
            os.unlink(path)
        except OSError as e:
            err = e

    if raise_error and err:
        raise err
    else:
        return err


def move(src_path, dest_path, raise_error=False):
    """
    Moves a file or folder from ``src_path`` to ``dest_path``. If either the source or
    the destination path no longer exist, this function does nothing. Any other
    exceptions are either raised or returned if ``raise_error`` is False.
    """
    err = None

    try:
        shutil.move(src_path, dest_path)
    except FileNotFoundError:
        # do nothing of source or dest path no longer exist
        pass
    except OSError as exc:
        err = exc

    if raise_error and err:
        raise err
    else:
        return err
