"""
Module to print neatly formatted tables and grids to the terminal.
"""
from __future__ import annotations

import sys
import os
import shutil


def get_term_size() -> os.terminal_size:
    """
    Returns the terminal size. If it cannot be determined, for example because output
    is piped to a file, return :attr:`sys.maxsize` for width and height instead.

    :returns: (width, height).
    """
    return shutil.get_terminal_size(fallback=(sys.maxsize, sys.maxsize))
