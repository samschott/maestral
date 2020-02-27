# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
import os
import shutil
from os import path as osp


def is_child(path, parent):
    """
    Checks if :param:`path` semantically is inside :param:`parent`. Neither path needs to
    refer to an actual item on the drive. This function is case sensitive.

    :param str path: Item path.
    :param str parent: Parent path.
    :returns: ``True`` if :param:`path` semantically lies inside :param:`parent`,
        ``False`` otherwise (including ``path == parent``).
    :rtype: bool
    """
    assert isinstance(path, str)
    assert isinstance(parent, str)

    parent = parent.rstrip(osp.sep)

    return path.startswith(parent + osp.sep) and not path == parent


def path_exists_case_insensitive(path, root="/"):
    """
    Checks if a `path` exists in given `root` directory, similar to
    `os.path.exists` but case-insensitive. If there are multiple
    case-insensitive matches, the first one is returned. If there is no match,
    an empty string is returned.

    :param str path: Relative path of item to find in the `root` directory.
    :param str root: Directory where we will look for `path`.
    :return: Absolute and case-sensitive path to search result on hard drive.
    :rtype: str
    """

    if not osp.isdir(root):
        raise ValueError("'{0}' is not a directory.".format(root))

    if path in ("", "/"):
        return root

    path_list = path.lstrip(osp.sep).split(osp.sep)
    path_list_lower = [x.lower() for x in path_list]

    i = 0
    local_paths = []
    for root, dirs, files in os.walk(root):
        for d in list(dirs):
            if not d.lower() == path_list_lower[i]:
                dirs.remove(d)
        for f in list(files):
            if not f.lower() == path_list_lower[i]:
                files.remove(f)

        local_paths = [osp.join(root, name) for name in dirs + files]

        i += 1
        if i == len(path_list_lower):
            break

    if len(local_paths) == 0:
        return ''
    else:
        return local_paths[0]


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
