# -*- coding: utf-8 -*-


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
