# -*- coding: utf-8 -*-

import warnings

__version__ = "1.3.2.dev0"
__author__ = "Sam Schott"
__url__ = "https://github.com/SamSchott/maestral"


# suppress Python 3.9 warning from rubicon-objc
warnings.filterwarnings("ignore", module="rubicon", category=UserWarning)
