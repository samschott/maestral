"""
This module contains functions for common path operations.
"""

# system imports
import os
import os.path as osp
import errno
import shutil
import itertools
import unicodedata
import fcntl
import platform
from stat import S_ISDIR
from typing import List, Optional, Tuple, Callable, Iterator, Iterable, Union

# local imports
from .hashing import DropboxContentHasher

F_GETPATH = 50


def _path_components(path: str) -> List[str]:
    components = path.strip(osp.sep).split(osp.sep)
    return [c for c in components if c]


_AnyPath = Union[str, bytes, "os.PathLike[str]", "os.PathLike[bytes]"]

# ==== path relationships ==============================================================


def is_child(path: str, parent: str, case_sensitive: bool = True) -> bool:
    """
    Checks if ``path`` semantically is inside ``parent``. Neither path needs to
    refer to an actual item on the drive. This function is case-sensitive.

    :param path: Item path.
    :param parent: Parent path.
    :param case_sensitive: Whether to do case-sensitive checks.
    :returns: Whether ``path`` semantically lies inside ``parent``.
    """
    if not case_sensitive:
        path = normalize(path)
        parent = normalize(parent)

    parent = parent.rstrip(osp.sep) + osp.sep
    path = path.rstrip(osp.sep)

    return path.startswith(parent)


def is_equal_or_child(path: str, parent: str, case_sensitive: bool = True) -> bool:
    """
    Checks if ``path`` semantically is inside ``parent`` or equals ``parent``. Neither
    path needs to refer to an actual item on the drive. This function is case-sensitive.

    :param path: Item path.
    :param parent: Parent path.
    :param case_sensitive: Whether to do case-sensitive checks.
    :returns: ``True`` if ``path`` semantically lies inside ``parent`` or
        ``path == parent``.
    """
    if not case_sensitive:
        path = normalize(path)
        parent = normalize(parent)

    return is_child(path, parent) or path == parent


# ==== case sensitivity and normalization ==============================================


def normalize_case(string: str) -> str:
    """
    Converts a string to lower case. Todo: Follow Python 2.5 / Dropbox conventions.

    :param string: Original string.
    :returns: Lowercase string.
    """
    return string.lower()


def normalize_unicode(string: str) -> str:
    """
    Normalizes a string to replace all decomposed unicode characters with their single
    character equivalents.

    :param string: Original string.
    :returns: Normalized string.
    """
    return unicodedata.normalize("NFC", string)


def normalize(string: str) -> str:
    """
    Replicates the path normalization performed by Dropbox servers. This typically only
    involves converting the path to lower case, with a few (undocumented) exceptions:

    * Unicode normalization: decomposed characters are converted to composed characters.
    * Lower casing of non-ascii characters: Dropbox uses the Python 2.5 behavior for
      conversion to lower case. This means that some cyrillic characters are incorrectly
      lower-cased. For example:
      "Ꙋ".lower() -> "Ꙋ" instead of "ꙋ"
      "ΣΣΣ".lower() -> "σσσ" instead of "σσς"
    * Trailing spaces are stripped from folder names. We do not perform this
      normalization here because the Dropbox API will raise sync errors for such names
      anyways.

    Note that calling :func:`normalize` on an already normalized path will return the
    unmodified input.

    Todo: Follow Python 2.5 / Dropbox conventions instead of Python 3 conventions.

    :param string: Original path.
    :returns: Normalized path.
    """
    return normalize_case(normalize_unicode(string))


def is_fs_case_sensitive(path: str) -> bool:
    """
    Checks if ``path`` lies on a partition with a case-sensitive file system.

    :param path: Path to check.
    :returns: Whether ``path`` lies on a partition with a case-sensitive file system.
    """
    if path == osp.pathsep:
        raise ValueError(f"Cannot check '{osp.pathsep}'")

    if path.islower():
        check_path = path.upper()
    else:
        check_path = path.lower()

    if exists(path) and not exists(check_path):
        return True
    else:
        return not osp.samefile(path, check_path)


def get_existing_equivalent_paths(
    path: str,
    root: str = osp.sep,
    norm_func: Callable[[str], str] = normalize,
) -> List[str]:
    """
    Given a "normalized" path using an injective (one-directional) normalization
    function, this method returns a list of matching un-normalized local paths. If no
    such local paths exist, list will be empty.

    :Example:

        Assume the normalization function is ``str.lower()``. If a root directory
        contains two folders "/parent/subfolder/child" and "/parent/Subfolder/child",
        two matches will be returned for "path = /parent/subfolder/child/file.txt".

    :param path: Normalized path relative to ``root``.
    :param root: Parent directory to search in. There are significant performance
        improvements if a root directory with a small tree is given.
    :param norm_func: Normalization function to use. Defaults to :func:`normalize`.
    :returns: List of existing paths for which `normalized(local_path) == normalized(path)`.
    """
    path = path.lstrip(osp.sep)

    if path == "":
        return [root]

    components = _path_components(path)
    n_components_root = len(_path_components(root))

    potential_candidates = {-1: [root]}

    for root, dirs, files in os.walk(root):
        n_components_current_root = len(_path_components(root))
        depth = n_components_current_root - n_components_root

        all_dirs = dirs.copy()
        all_files = files.copy()

        dirs.clear()
        files.clear()

        # If current path is too deep to be match, skip it.
        if depth >= len(components):
            continue

        component_normalized = norm_func(components[depth])

        for dirname in all_dirs:
            if norm_func(dirname) == component_normalized:
                dirs.append(dirname)

        # Only check files if we are at the end of the path.
        if depth + 1 == len(components):
            for filename in all_files:
                if norm_func(filename) == component_normalized:
                    files.append(filename)

        new_candidates = [osp.join(root, name) for name in itertools.chain(dirs, files)]

        if new_candidates:
            potential_candidates[depth] = (
                potential_candidates.get(depth, []) + new_candidates
            )

    i_max = max(potential_candidates.keys())

    if i_max + 1 == len(components):
        return potential_candidates[i_max]
    return []


def _macos_get_canonically_cased_path(path: str) -> str:
    # Use fcntl to get FS path, there can only be one.
    fd = open(path, "a", opener=opener_no_symlink)
    fs_path = fcntl.fcntl(fd.fileno(), F_GETPATH, b"\x00" * 1024)
    return os.fsdecode(fs_path.strip(b"\x00"))


def to_existing_unnormalized_path(
    path: str, root: str = osp.sep, norm_func: Callable[[str], str] = normalize
) -> str:
    """
    Returns a cased version of the given path if corresponding nodes (with arbitrary
    casing) exist in the given root directory. If multiple matches are found, only one
    is returned.

    This is similar to :func:`get_existing_equivalent_paths` but returns only the first
    candidate or raises a :class:`FileNotFoundError` if no candidates can be found.

    If the file system is not case-sensitive but case-preserving, this function
    effectively returns the "displayed" version of a path, as used for example in file
    managers.

    On macOS, we use fcntl F_GETPATH for a more efficient implementation.

    :param path: Original path relative to ``root``.
    :param root: Parent directory to search in. There are significant performance
        improvements if a root directory with a small tree is given.
    :param norm_func: Normalization function to use. Defaults to :func:`normalize`.
    :returns: Absolute and cased version of given path.
    :raises FileNotFoundError: if ``path`` does not exist in root ``root`` or ``root``
        itself does not exist.
    """
    if platform.system() == "Darwin" and norm_func is normalize:
        try:
            return _macos_get_canonically_cased_path(path)
        except FileNotFoundError:
            raise
        except OSError:
            # Fall back to cross-platform method.
            pass

    candidates = get_existing_equivalent_paths(path, root)

    if len(candidates) == 0:
        raise FileNotFoundError(f'No matches with different casing found in "{root}"')
    return candidates[0]


def normalized_path_exists(path: str, root: str = osp.sep) -> bool:
    """
    Checks if a ``path`` exists in given ``root`` directory, similar to
    ``os.path.exists`` but case-insensitive. Normalisation is performed as by Dropbox
    servers (lower case and unicode normalisation).

    :param path: Path relative to ``root``.
    :param root: Directory where we will look for ``path``. There are significant
        performance improvements if a root directory with a small tree is given.
    :returns: Whether an arbitrarily cased version of ``path`` exists.
    """
    candidates = get_existing_equivalent_paths(path, root)

    for c in candidates:
        if exists(c):
            return True

    return False


def generate_cc_name(path: str, suffix: str) -> str:
    """
    Generates a path for a conflicting copy of ``path``. The file name is created by
    inserting the given ``suffix`` between the filename and the extension. For example,
    for ``suffix = "conflicting copy"``:

        "my_file.txt" -> "my_file (conflicting copy).txt"

    If a file with the resulting path already exists (case-insensitive!), we
    additionally append an integer number, for instance:

        "my_file.txt" -> "my_file (conflicting copy 1).txt"

    :param path: Original path name.
    :param suffix: Suffix to use.
    :returns: New path.
    """
    dirname, basename = osp.split(path)
    filename, ext = osp.splitext(basename)

    i = 0
    cc_candidate = f"{filename} ({suffix}){ext}"

    while normalized_path_exists(cc_candidate, dirname):
        i += 1
        cc_candidate = f"{filename} ({suffix} {i}){ext}"

    return osp.join(dirname, cc_candidate)


# ==== higher level file operations ====================================================


def delete(
    path: str, force_case_sensitive: bool = False, raise_error: bool = False
) -> Optional[OSError]:
    """
    Deletes a file or folder at ``path``. Symlinks will not be followed.

    :param path: Path of item to delete.
    :param force_case_sensitive: Whether to perform the deletion only if the item
        appears with the same casing as provided in `path`. This can be used on
        case-insensitive but preserving file systems to ensure that the intended item is
        deleted.
    :param raise_error: Whether to raise errors or return them.
    :returns: Any caught exception during the deletion.
    """
    err: Optional[OSError] = None

    if force_case_sensitive and not equal_but_for_unicode_norm(
        path, to_existing_unnormalized_path(path)
    ):
        err = FileNotFoundError(f"No such file '{path}'")
        if raise_error:
            raise err
        else:
            return err

    try:
        shutil.rmtree(path)  # Will raise OSError when it finds a symlink.
    except OSError:
        try:
            os.unlink(path)  # Does not follow symlinks.
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
    preserve_dest_permissions: bool = False,
) -> Optional[OSError]:
    """
    Moves a file or folder from ``src_path`` to ``dest_path``. If either the source or
    the destination path no longer exist, this function does nothing. Any other
    exceptions are either raised or returned if ``raise_error`` is False.

    Uses ``os.rename`` internally.

    :param src_path: Path of item to move.
    :param dest_path: Destination path. Any existing file at this path will be replaced
        by the move. Any existing **empty** folder will be replaced if the source is
        also a folder.
    :param raise_error: Whether to raise errors or return them.
    :param preserve_dest_permissions: Whether to apply the permissions of the source
        path to the destination path. Permissions will not be set recursively.
    :returns: Any caught exception during the move.
    """
    err: Optional[OSError] = None
    orig_mode: Optional[int] = None

    if preserve_dest_permissions:
        # save dest permissions
        try:
            orig_mode = os.lstat(dest_path).st_mode & 0o777
        except FileNotFoundError:
            pass

    try:
        os.rename(src_path, dest_path)
    except FileNotFoundError:
        # do nothing if source or dest path no longer exist
        pass
    except OSError as exc:
        err = exc
    else:
        if orig_mode:
            # reapply dest permissions
            try:
                if os.chmod in os.supports_follow_symlinks:
                    os.chmod(dest_path, orig_mode, follow_symlinks=False)
                else:
                    os.chmod(dest_path, orig_mode)
            except OSError:
                pass

    if raise_error and err:
        raise err
    else:
        return err


def walk(
    root: str,
    listdir: Callable[[str], Iterable["os.DirEntry[str]"]] = os.scandir,
) -> Iterator[Tuple[str, os.stat_result]]:
    """
    Iterates recursively over the content of a folder.

    :param root: Root folder to walk.
    :param listdir: Function to call to get the folder content.
    :returns: Iterator over (path, stat) results.
    """
    for entry in listdir(root):
        try:
            path = entry.path
            stat = entry.stat(follow_symlinks=False)

            yield path, stat

            if S_ISDIR(stat.st_mode):
                yield from walk(entry.path, listdir)

        except OSError as exc:
            # Directory may have been deleted between finding it in the directory
            # list of its parent and trying to list its contents. If this
            # happens we treat it as empty. Likewise, if the directory was replaced
            # with a file of the same name (less likely, but possible), it will be
            # treated as empty.
            if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.EINVAL):
                return
            else:
                raise


# ==== miscellaneous utilities =========================================================


def content_hash(
    local_path: str, chunk_size: int = 65536
) -> Tuple[Optional[str], Optional[float]]:
    """
    Computes content hash of a local file.

    :param local_path: Absolute path on local drive.
    :param chunk_size: Size of chunks to hash in bytes.
    :returns: Content hash to compare with Dropbox's content hash and mtime just before
        the hash was computed.
    """
    hasher = DropboxContentHasher()

    try:
        mtime = os.lstat(local_path).st_mtime

        try:
            with open(local_path, "rb", opener=opener_no_symlink) as f:
                while True:
                    chunk = f.read(chunk_size)
                    if len(chunk) == 0:
                        break
                    hasher.update(chunk)

        except IsADirectoryError:
            return "folder", mtime

        except OSError as exc:
            if exc.errno == errno.ELOOP:
                hasher.update(b"")  # use empty file for symlinks
            else:
                raise exc

        return str(hasher.hexdigest()), mtime

    except FileNotFoundError:
        return None, None
    except NotADirectoryError:
        # a parent directory in the path refers to a file instead of a folder
        return None, None
    finally:
        del hasher


def fs_max_lengths_for_path(path: str = "/") -> Tuple[int, int]:
    """
    Return the maximum length of file names and paths allowed on a file system.

    :param path: Path to check. This can be specified because different paths may be
        residing on different file systems. If the given path does not exist, the first
        existing parent directory in the tree be taken.
    :returns: Tuple giving the maximum file name and total path lengths.
    """
    path = osp.abspath(path)
    dirname = osp.dirname(path)

    while True:
        try:
            max_char_path = os.pathconf(dirname, "PC_PATH_MAX")
            max_char_name = os.pathconf(dirname, "PC_NAME_MAX")
            return max_char_path, max_char_name
        except (FileNotFoundError, NotADirectoryError):
            dirname = osp.dirname(dirname)
        except ValueError:
            raise RuntimeError("Cannot get file length limits.")
        except OSError as exc:
            if exc.errno == errno.EINVAL:
                raise RuntimeError("Cannot get file length limits.")
            else:
                dirname = "/"


# ==== symlink-proof os methods ========================================================


def opener_no_symlink(path: _AnyPath, flags: int) -> int:
    """
    Opener that does not follow symlinks. Uses :meth:`os.open` under the hood.

    :param path: Path to open.
    :param flags: Flags passed to :meth:`os.open`. O_NOFOLLOW will be added.
    :return: Open file descriptor.
    """
    flags |= os.O_NOFOLLOW
    return os.open(path, flags=flags)


def _get_stats_no_symlink(path: _AnyPath) -> Optional[os.stat_result]:
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None


def exists(path: _AnyPath) -> bool:
    """Returns whether an item exists at the path. Returns True for symlinks."""
    return _get_stats_no_symlink(path) is not None


def isfile(path: _AnyPath) -> bool:
    """Returns whether a file exists at the path. Returns True for symlinks."""
    stat = _get_stats_no_symlink(path)

    if stat is None:
        return False
    else:
        return not S_ISDIR(stat.st_mode)


def isdir(path: _AnyPath) -> bool:
    """Returns whether a folder exists at the path. Returns False for symlinks."""
    stat = _get_stats_no_symlink(path)

    if stat is None:
        return False
    else:
        return S_ISDIR(stat.st_mode)


def getsize(path: _AnyPath) -> int:
    """Returns the size. Returns False for symlinks."""
    return os.lstat(path).st_size


def equal_but_for_unicode_norm(s0: str, s1: str) -> bool:
    return normalize_unicode(s0) == normalize_unicode(s1)


def get_symlink_target(local_path: str) -> Optional[str]:
    """
    Returns the symlink target of a file.

    :param local_path: Absolute path on local drive.
    :returns: Symlink target of local file. None if the local path does not refer to
        a symlink or does not exist.
    """
    try:
        return os.readlink(local_path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as err:
        if err.errno == errno.EINVAL:
            # File is not a symlink.
            return None

        if err.errno == errno.ENAMETOOLONG:
            # Path cannot exist on this filesystem.
            return None

        raise err
