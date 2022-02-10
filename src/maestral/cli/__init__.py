"""
This module contains the command line interface of Maestral. Keep all heavy imports
local to the command or method that requires them to ensure a responsive CLI.
"""

from .cli_main import main

__all__ = ["main"]
