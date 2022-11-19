"""
Module to print neatly formatted tables and grids to the terminal.
"""

import shutil
import sys


def get_term_width() -> int:
    """
    Returns the terminal width. If it cannot be determined, for example because output
    is piped to a file, return :attr:`sys.maxsize` instead.

    :returns: Terminal width.
    """
    term_size = shutil.get_terminal_size(fallback=(sys.maxsize, sys.maxsize))
    return term_size.columns
