# -*- coding: utf-8 -*-
"""

The following APIs should remain stable for frontends:

* maestral.main.Maestral
* maestral.constants
* maestral.daemon
* maestral.errors
* maestral.utils.appdirs
* maestral.utils.autostart

"""

import warnings

__version__ = "1.3.0"
__author__ = "Sam Schott"
__url__ = "https://github.com/SamSchott/maestral"


# suppress Python 3.9 warning from rubicon-objc
warnings.filterwarnings("ignore", module="rubicon", category=UserWarning)
