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
import itertools


def _path_components(path):
    components = path.strip(osp.sep).split(osp.sep)
    cleaned_components = [c for c in components if c]
    return cleaned_components


def is_fs_case_sensitive(path):
    """
    Checks if ``path`` lies on a partition with a case-sensitive file system.

    :param str path: Path to check.
    :returns: Whether ``path`` lies on a partition with a case-sensitive file system.
    :rtype: bool
    """
    if path.islower():
        check_path = path.upper()
    else:
        check_path = path.lower()

    if osp.exists(path) and not osp.exists(check_path):
        return True
    else:
        return not osp.samefile(path, check_path)


def is_child(path, parent):
    """
    Checks if ``path`` semantically is inside ``parent``. Neither path needs to
    refer to an actual item on the drive. This function is case sensitive.

    :param str path: Item path.
    :param str parent: Parent path.
    :returns: Whether ``path`` semantically lies inside ``parent``.
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


def cased_path_candidates(path, root=osp.sep, is_fs_case_sensitive=True):
    """
    Returns a list of cased versions of the given path as far as corresponding nodes
    exist in the given root directory. For instance, if a case sensitive root directory
    contains two folders "/parent/subfolder/child" and "/parent/Subfolder/child",
    there will be two matches for "/parent/subfolder/child/file.txt". If the root
    directory does not exist, only one candidate ``os.path.join(root, path)`` is returned.

    :param str path: Original path relative to ``root``.
    :param str root: Parent directory to search in. There are significant
        performance improvements if a root directory with a small tree is given.
    :param bool is_fs_case_sensitive: Bool indicating if the file system is case
        sensitive. If ``False``, we know that there can be at most one match and choose
        a faster algorithm.
    :returns: Candidates for correctly cased local paths.
    :rtype: list[str]
    """

    path = path.lstrip(osp.sep)

    if path == '':
        return [root]

    path_list = _path_components(path)
    n_components = len(path_list)
    n_components_root = len(_path_components(root))

    candidates = {-1: [root]}

    for root, dirs, files in os.walk(root):

        n_components_current_root = len(_path_components(root))
        depth = n_components_current_root - n_components_root

        all_dirs = dirs.copy()
        all_files = files.copy()

        dirs.clear()
        files.clear()

        if depth >= n_components:
            if is_fs_case_sensitive:
                continue
            else:
                break

        found = False
        path_lower = path_list[depth].lower()

        for d in all_dirs:
            if d.lower() == path_lower:
                dirs.append(d)

                if not is_fs_case_sensitive:
                    # skip to next iteration since there can be no more matches
                    found = True
                    break

        if depth + 1 == n_components and not found:
            # look at files
            for f in all_files:
                if f.lower() == path_lower:
                    files.append(f)

                    if not is_fs_case_sensitive:
                        # skip to next iteration since there can be no more matches
                        break

        new_candidates = [osp.join(root, name) for name in itertools.chain(dirs, files)]

        if new_candidates:
            try:
                candidates[depth].extend(new_candidates)
            except KeyError:
                candidates[depth] = new_candidates

    i_max = max(candidates.keys())
    local_paths = [osp.join(node, *path_list[i_max + 1:]) for node in candidates[i_max]]

    return local_paths


def to_cased_path(path, root=osp.sep, is_fs_case_sensitive=True):
    """
    Returns a cased version of the given path as far as corresponding nodes exist in the
    given root directory. If multiple matches are found, only one is returned. If ``path``
    does not exist in root ``root`` or ``root`` does not exist, the return value will be
    ``os.path.join(root, path)``.

    :param str path: Original path relative to ``root``.
    :param str root: Parent directory to search in. There are significant
        performance improvements if a root directory with a small tree is given.
    :param bool is_fs_case_sensitive: Bool indicating if the file system is case
        sensitive. If ``False``, we know that there can be at most one match and choose
        a faster algorithm.
    :returns: Candidates for c
    :returns: Absolute and cased version of given path.
    :rtype: str
    """

    candidates = cased_path_candidates(path, root, is_fs_case_sensitive)
    return candidates[0]


def path_exists_case_insensitive(path, root=osp.sep, is_fs_case_sensitive=True):
    """
    Checks if a ``path`` exists in given ``root`` directory, similar to ``os.path.exists``
    but case-insensitive.

    :param str path: Path relative to ``root``.
    :param str root: Directory where we will look for ``path``. There are significant
        performance improvements if a root directory with a small tree is given.
    :param bool is_fs_case_sensitive: Bool indicating if the file system is case
        sensitive. If ``False``, we know that there can be at most one match and choose
        a faster algorithm.
    :returns: Whether an arbitrarily cased version of ``path`` exists.
    :rtype: bool
    """

    if is_fs_case_sensitive:

        candidates = cased_path_candidates(path, root, is_fs_case_sensitive)

        for c in candidates:
            if osp.exists(c):
                return True

        return False

    else:
        return osp.exists(osp.join(root, path.lstrip(osp.sep)))


def generate_cc_name(path, suffix='conflicting copy', is_fs_case_sensitive=True):
    """
    Generates a path for a conflicting copy of ``path``. The file name is created by
    inserting the given ``suffix`` between the the filename and extension. For instance:

        'my_file.txt' -> 'my_file (conflicting copy).txt'

    If a file with the resulting path already exists (case-insensitive!), we additionally
    append an integer number, for instance:

        'my_file.txt' -> 'my_file (conflicting copy 1).txt'

    :param str path: Original path name.
    :param str suffix: Suffix to use. Defaults to 'conflicting copy'.
    :param bool is_fs_case_sensitive: Bool indicating if the file system is case
        sensitive. If ``False``, we know that there can be at most one match and choose
        a faster algorithm.
    :returns: New path.
    :rtype: str
    """

    dirname, basename = osp.split(path)
    filename, ext = osp.splitext(basename)

    i = 0
    cc_candidate = f'{filename} ({suffix}){ext}'

    while path_exists_case_insensitive(cc_candidate, dirname, is_fs_case_sensitive):
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
