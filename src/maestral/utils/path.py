# -*- coding: utf-8 -*-
"""
This module contains functions for common path operations.
"""

# system imports
import os
import os.path as osp
import shutil
import itertools
from typing import List, Optional, Tuple

# local imports
from .content_hasher import DropboxContentHasher


def _path_components(path: str) -> List[str]:
    components = path.strip(osp.sep).split(osp.sep)
    cleaned_components = [c for c in components if c]
    return cleaned_components


def is_fs_case_sensitive(path: str) -> bool:
    """
    Checks if ``path`` lies on a partition with a case-sensitive file system.

    :param path: Path to check.
    :returns: Whether ``path`` lies on a partition with a case-sensitive file system.
    """
    if path.islower():
        check_path = path.upper()
    else:
        check_path = path.lower()

    if osp.exists(path) and not osp.exists(check_path):
        return True
    else:
        return not osp.samefile(path, check_path)


def is_child(path: str, parent: str) -> bool:
    """
    Checks if ``path`` semantically is inside ``parent``. Neither path needs to
    refer to an actual item on the drive. This function is case sensitive.

    :param path: Item path.
    :param parent: Parent path.
    :returns: Whether ``path`` semantically lies inside ``parent``.
    """

    parent = parent.rstrip(osp.sep) + osp.sep
    path = path.rstrip(osp.sep)

    return path.startswith(parent)


def is_equal_or_child(path: str, parent: str) -> bool:
    """
    Checks if ``path`` semantically is inside ``parent`` or equals ``parent``. Neither
    path needs to refer to an actual item on the drive. This function is case sensitive.

    :param path: Item path.
    :param parent: Parent path.
    :returns: ``True`` if ``path`` semantically lies inside ``parent`` or
        ``path == parent``.
    """

    return is_child(path, parent) or path == parent


def cased_path_candidates(
    path: str, root: str = osp.sep, is_fs_case_sensitive: bool = True
) -> List[str]:
    """
    Returns a list of cased versions of the given path as far as corresponding nodes
    exist in the given root directory. For instance, if a case sensitive root directory
    contains two folders "/parent/subfolder/child" and "/parent/Subfolder/child", there
    will be two matches for "/parent/subfolder/child/file.txt". If the root directory
    does not exist, only one candidate ``os.path.join(root, path)`` is returned.

    :param path: Original path relative to ``root``.
    :param root: Parent directory to search in. There are significant performance
        improvements if a root directory with a small tree is given.
    :param is_fs_case_sensitive: Bool indicating if the file system is case sensitive.
        If ``False``, we know that there can be at most one match and choose a faster
        algorithm.
    :returns: Candidates for correctly cased local paths.
    """

    path = path.lstrip(osp.sep)

    if path == "":
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
    local_paths = [
        osp.join(node, *path_list[i_max + 1 :]) for node in candidates[i_max]
    ]

    return local_paths


def to_cased_path(
    path: str, root: str = osp.sep, is_fs_case_sensitive: bool = True
) -> str:
    """
    Returns a cased version of the given path as far as corresponding nodes (with
    arbitrary casing) exist in the given root directory. If multiple matches are found,
    only one is returned. If ``path`` does not exist in root ``root`` or ``root`` does
    not exist, the return value will be ``os.path.join(root, path)``.

    :param path: Original path relative to ``root``.
    :param root: Parent directory to search in. There are significant performance
        improvements if a root directory with a small tree is given.
    :param is_fs_case_sensitive: Bool indicating if the file system is case sensitive.
        If ``False``, we know that there can be at most one match and choose a faster
        algorithm.
    :returns: Absolute and cased version of given path.
    """

    candidates = cased_path_candidates(path, root, is_fs_case_sensitive)
    return candidates[0]


def to_existing_cased_path(
    path: str, root: str = osp.sep, is_fs_case_sensitive: bool = True
) -> str:
    """
    Returns a cased version of the given path if corresponding nodes (with arbitrary
    casing) exist in the given root directory. If multiple matches are found, only one
    is returned.

    :param path: Original path relative to ``root``.
    :param root: Parent directory to search in. There are significant performance
        improvements if a root directory with a small tree is given.
    :param is_fs_case_sensitive: Bool indicating if the file system is case sensitive.
        If ``False``, we know that there can be at most one match and choose a faster
        algorithm.
    :returns: Absolute and cased version of given path.
    :raises FileNotFoundError: if ``path`` does not exist in root ``root`` or ``root``
        itself does not exist.
    """

    candidates = cased_path_candidates(path, root, is_fs_case_sensitive)

    for candidate in candidates:
        if osp.exists(candidate):
            return candidate

    raise FileNotFoundError(f'No matches with different casing found in "{root}"')


def path_exists_case_insensitive(
    path: str, root: str = osp.sep, is_fs_case_sensitive: bool = True
) -> bool:
    """
    Checks if a ``path`` exists in given ``root`` directory, similar to
    ``os.path.exists`` but case-insensitive.

    :param path: Path relative to ``root``.
    :param root: Directory where we will look for ``path``. There are significant
        performance improvements if a root directory with a small tree is given.
    :param is_fs_case_sensitive: Bool indicating if the file system is case sensitive.
        If ``False``, we know that there can be at most one match and choose a faster
        algorithm.
    :returns: Whether an arbitrarily cased version of ``path`` exists.
    """

    if is_fs_case_sensitive:

        candidates = cased_path_candidates(path, root, is_fs_case_sensitive)

        for c in candidates:
            if osp.exists(c):
                return True

        return False

    else:
        return osp.exists(osp.join(root, path.lstrip(osp.sep)))


def generate_cc_name(
    path: str, suffix: str = "conflicting copy", is_fs_case_sensitive: bool = True
) -> str:
    """
    Generates a path for a conflicting copy of ``path``. The file name is created by
    inserting the given ``suffix`` between the the filename and extension. For instance:

        "my_file.txt" -> "my_file (conflicting copy).txt"

    If a file with the resulting path already exists (case-insensitive!), we
    additionally append an integer number, for instance:

        "my_file.txt" -> "my_file (conflicting copy 1).txt"

    :param path: Original path name.
    :param suffix: Suffix to use. Defaults to "conflicting copy".
    :param is_fs_case_sensitive: Bool indicating if the file system is case sensitive.
        If ``False``, we know that there can be at most one match and choose a faster
        algorithm.
    :returns: New path.
    """

    dirname, basename = osp.split(path)
    filename, ext = osp.splitext(basename)

    i = 0
    cc_candidate = f"{filename} ({suffix}){ext}"

    while path_exists_case_insensitive(cc_candidate, dirname, is_fs_case_sensitive):
        i += 1
        cc_candidate = f"{filename} ({suffix} {i}){ext}"

    return osp.join(dirname, cc_candidate)


def delete(path: str, raise_error: bool = False) -> Optional[OSError]:
    """
    Deletes a file or folder at ``path``.

    :param path: Path of item to delete.
    :param raise_error: If ``True``, raise any OSErrors. If ``False``, catch OSErrors
        and return them.
    :returns: Any caught exception during the deletion.
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


def move(
    src_path: str,
    dest_path: str,
    raise_error: bool = False,
    preserve_dest_permissions=False,
) -> Optional[OSError]:
    """
    Moves a file or folder from ``src_path`` to ``dest_path``. If either the source or
    the destination path no longer exist, this function does nothing. Any other
    exceptions are either raised or returned if ``raise_error`` is False.

    :param src_path: Path of item to move.
    :param dest_path: Destination path. Any existing file at this path will be replaced
        by the move. Any existing **empty** folder will be replaced if the source is
        also a folder.
    :param raise_error: If ``True``, raise any OSErrors. If ``False``, catch OSErrors
        and return them.
    :param preserve_dest_permissions: If ``True``, attempt to preserve the permissions
        of any file at the destination. If ``False``, the permissions of src_path will
        be used.
    :returns: Any caught exception during the move.
    """

    err: Optional[OSError] = None
    orig_mode: Optional[int] = None

    if preserve_dest_permissions:
        # save dest permissions
        try:
            orig_mode = os.stat(dest_path).st_mode & 0o777
        except FileNotFoundError:
            pass

    try:
        shutil.move(src_path, dest_path)
    except FileNotFoundError:
        # do nothing of source or dest path no longer exist
        pass
    except OSError as exc:
        err = exc
    else:
        if orig_mode:
            # reapply dest permissions
            try:
                os.chmod(dest_path, orig_mode)
            except OSError:
                pass

    if raise_error and err:
        raise err
    else:
        return err


def content_hash(
    local_path: str, chunk_size: int = 1024
) -> Tuple[Optional[str], Optional[float]]:
    """
    Computes content hash of a local file.

    :param local_path: Absolute path on local drive.
    :param chunk_size: Size of chunks to hash in bites.
    :returns: Content hash to compare with Dropbox's content hash and mtime just before
        the hash was computed.
    """

    hasher = DropboxContentHasher()

    try:
        mtime = os.stat(local_path).st_mtime

        try:
            with open(local_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if len(chunk) == 0:
                        break
                    hasher.update(chunk)

        except IsADirectoryError:
            return "folder", mtime
        else:
            return str(hasher.hexdigest()), mtime

    except FileNotFoundError:
        return None, None
    except NotADirectoryError:
        # a parent directory in the path refers to a file instead of a folder
        return None, None
    finally:
        del hasher
