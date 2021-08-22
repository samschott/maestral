# -*- coding: utf-8 -*-

import warnings

from click import shell_completion  # type: ignore


__version__ = "1.4.8"
__author__ = "Sam Schott"
__url__ = "https://maestral.app"


# suppress Python 3.9 warning from rubicon-objc
warnings.filterwarnings("ignore", module="rubicon", category=UserWarning)


# patch click shell completion argument detection
# see https://github.com/pallets/click/issues/1929


def _start_of_option(value: str) -> bool:
    """Check if the value looks like the start of an option."""
    return value[0] == "-" if value else False


shell_completion._start_of_option = _start_of_option
