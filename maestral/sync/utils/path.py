# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
import os
import shutil
from os import path as osp


def is_child(path1, path2):
    """
    Checks if :param:`path1` semantically is inside folder :param:`path2`. Neither
    path must refer to an actual item on the drive. This function is case sensitive.

    :param str path1: Folder path.
    :param str path2: Parent folder path.
    :returns: ``True`` if :param:`path1` semantically is a subfolder of :param:`path2`,
        ``False`` otherwise (including ``path1 == path2``.
    :rtype: bool
    """
    assert isinstance(path1, str)
    assert isinstance(path2, str)

    path2.rstrip(osp.sep)

    return path1.startswith(path2 + osp.sep) and not path1 == path2


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

    if path in ["", "/"]:
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


def delete_file_or_folder(path, return_error=False):
    """
    Deletes a file or folder at :param:`path`. Returns True on success,
    False otherwise.
    """
    success = True
    err = None

    try:
        shutil.rmtree(path)
    except OSError:
        try:
            os.unlink(path)
        except OSError as e:
            success = False
            err = e

    if return_error:
        return success, err
    else:
        return success