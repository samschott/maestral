# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
import os
from os import path as osp
import shutil


def is_child(path, parent):
    """
    Checks if :param:`path` semantically is inside :param:`parent`. Neither path needs to
    refer to an actual item on the drive. This function is case sensitive.

    :param str path: Item path.
    :param str parent: Parent path.
    :returns: ``True`` if :param:`path` semantically lies inside :param:`parent` or
        ``path == parent``, ``False`` otherwise.
    :rtype: bool
    """

    parent = parent.rstrip(osp.sep) + os.sep
    path = path.rstrip(osp.sep)

    return path.startswith(parent)


def is_equal_or_child(path, parent):
    """
    Checks if ``path`` semantically is inside ``parent`` or equals ``parent``. Neither
    path needs to refer to an actual item on the drive. This function is case sensitive.

    :param str path: Item path.
    :param str parent: Parent path.
    :returns: ``True`` if :param:`path` semantically lies inside :param:`parent`,
        ``False`` otherwise (including ``path == parent``).
    :rtype: bool
    """

    return is_child(path, parent) or path == parent


def path_exists_case_insensitive(path, root='/'):
    """
    Checks if a `path` exists in given `root` directory, similar to `os.path.exists` but
    case-insensitive. A list of all case-insensitive matches is returned.

    :param str path: Path relative to the `root` directory.
    :param str root: Directory where we will look for `path`. There are significant
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

    :param str path:
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
    Deletes a file or folder at :param:`path`. Returns any caught
    exceptions on failure or None on success.
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
