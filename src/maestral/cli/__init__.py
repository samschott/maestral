"""
This module contains the command line interface of Maestral. Keep all heavy imports
local to the command or method that requires them to ensure a responsive CLI.
"""

from .cli_main import main
from .utils import freeze_support

__all__ = ["main", "freeze_support"]
