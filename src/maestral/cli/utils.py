"""
Module to print neatly formatted tables and grids to the terminal.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys


def get_term_size() -> os.terminal_size:
    """
    Returns the terminal size. If it cannot be determined, for example because output
    is piped to a file, return :attr:`sys.maxsize` for width and height instead.

    :returns: (width, height).
    """
    return shutil.get_terminal_size(fallback=(sys.maxsize, sys.maxsize))


def freeze_support():
    """
    Provides support to start the CLI from a frozen executable.
    """

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--cli", action="store_true")
    parsed_args, remaining = parser.parse_known_args()

    if parsed_args.cli:
        from .cli_main import main

        sys.argv = ["maestral"] + remaining
        main(prog_name="maestral")
        sys.exit()
