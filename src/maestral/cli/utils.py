"""
Module to print neatly formatted tables and grids to the terminal.
"""

import shutil
import sys
from datetime import datetime


def get_term_width() -> int:
    """
    Returns the terminal width. If it cannot be determined, for example because output
    is piped to a file, return :attr:`sys.maxsize` instead.

    :returns: Terminal width.
    """
    term_size = shutil.get_terminal_size(fallback=(sys.maxsize, sys.maxsize))
    return term_size.columns


def datetime_from_iso_str(time_str: str) -> datetime:
    """
    Converts an ISO 8601 time string such as '2015-05-15T15:50:38Z' to a timezone aware
    datetime object in the local time zone.

    :param: ISO 8601 time string.
    :returns: Datetime object.
    """

    # replace Z with +0000, required for Python 3.6 compatibility
    time_str = time_str.replace("Z", "+0000")
    return datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S%z").astimezone()
