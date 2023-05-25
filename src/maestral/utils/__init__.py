"""Utility modules and functions"""

from __future__ import annotations

import os
from types import TracebackType
from typing import Iterator, TypeVar, Optional, Iterable, Tuple, Type

from packaging.version import Version

from .path import normalize


_N = TypeVar("_N", float, int)
_T = TypeVar("_T")
_ExecInfoType = Tuple[Type[BaseException], BaseException, Optional[TracebackType]]


def natural_size(num: float, unit: str = "B", sep: bool = True) -> str:
    """
    Convert number to a human-readable string with decimal prefix.

    :param float num: Value in given unit.
    :param unit: Unit suffix.
    :param sep: Whether to separate unit and value with a space.
    :returns: Human-readable string with decimal prefixes.
    """
    sep_char = " " if sep else ""

    for prefix in ("", "K", "M", "G"):
        if abs(num) < 1000.0:
            return f"{num:3.1f}{sep_char}{prefix}{unit}"
        num /= 1000.0

    prefix = "T"
    return f"{num:.1f}{sep_char}{prefix}{unit}"


def chunks(lst: list[_T], n: int, consume: bool = False) -> Iterator[list[_T]]:
    """
    Partitions a list into chunks of length ``n``.

    :param lst: Iterable to partition.
    :param n: Chunk size.
    :param consume: If True, the list will be consumed (emptied) during the iteration.
    :returns: Iterator over chunks.
    """
    if consume:
        while lst:
            chunk = lst[0:n]
            del lst[0:n]
            yield chunk
    else:
        for i in range(0, len(lst), n):
            yield lst[i : i + n]


def clamp(n: _N, minn: _N, maxn: _N) -> _N:
    """
    Clamps a number between a minimum and maximum value.

    :param n: Original value.
    :param minn: Minimum allowed value.
    :param maxn: Maximum allowed value.
    :returns: Clamped value.
    """
    if n > maxn:
        return maxn
    elif n < minn:
        return minn
    else:
        return n


def get_newer_version(version: str, releases: Iterable[str]) -> Optional[str]:
    """
    Checks a given release version against a version list of releases to see if an
    update is available. Only offers newer versions if they are not a prerelease.

    :param version: The current version.
    :param releases: A list of valid cleaned releases.
    :returns: The version string of the latest release if a newer release is available.
    """
    releases = [r for r in releases if not Version(r).is_prerelease]
    releases.sort(key=Version)
    latest_release = releases[-1]

    return latest_release if Version(version) < Version(latest_release) else None


def removeprefix(string: str, prefix: str, case_sensitive: bool = True) -> str:
    """
    Removes the given prefix from a string. Only the first instance of the prefix is
    removed. The original string is returned if it does not start with the given prefix.

    This follows the Python 3.9 implementation of ``str.removeprefix``.

    :param string: Original string.
    :param prefix: Prefix to remove.
    :param case_sensitive: Whether to do case-sensitive prefix removal.
    :returns: String without prefix.
    """
    if case_sensitive:
        string_lower = string
        prefix_lower = prefix
    else:
        string_lower = normalize(string)
        prefix_lower = normalize(prefix)

    if string_lower.startswith(prefix_lower):
        return string[len(prefix) :]
    else:
        raise ValueError(f'"{string}" does not start with "{prefix}"')


def sanitize_string(string: str) -> str:
    """
    Converts a string provided by file system APIs, which may contain surrogate escapes
    for bytes with unknown encoding, to a string which can always be displayed or
    printed. This is done by replacing invalid characters with "�".

    :param string: Original string.
    :returns: Sanitised path where all surrogate escapes have been replaced with "�".
    """
    return os.fsencode(string).decode(errors="replace")


def exc_info_tuple(exc: BaseException) -> _ExecInfoType:
    """Creates an exc-info tuple from an exception."""
    return type(exc), exc, exc.__traceback__
