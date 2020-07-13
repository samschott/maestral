# -*- coding: utf-8 -*-
from typing import List, Iterator, TypeVar


_T = TypeVar('_T')


def natural_size(num: float, unit: str = 'B', sep: bool = True) -> str:
    """
    Convert number to a human readable string with decimal prefix.

    :param float num: Value in given unit.
    :param unit: Unit suffix.
    :param sep: Whether to separate unit and value with a space.
    :returns: Human readable string with decimal prefixes.
    """
    sep_char = ' ' if sep else ''

    for prefix in ('', 'K', 'M', 'G'):
        if abs(num) < 1000.0:
            return f'{num:3.1f}{sep_char}{prefix}{unit}'
        num /= 1000.0

    prefix = 'T'
    return f'{num:.1f}{sep_char}{prefix}{unit}'


def chunks(lst: List, n: int) -> Iterator[List]:
    """
    Partitions an iterable into chunks of length ``n``.

    :param lst: Iterable to partition.
    :param n: Chunk size.
    :returns: Iterator over chunks.
    """
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def clamp(n: _T, minn: _T, maxn: _T) -> _T:
    return max(min(maxn, n), minn)
