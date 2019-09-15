# -*- coding: utf-8 -*-

import sys
import platform

is_macos_bundle = getattr(sys, "frozen", False) and platform.system() == "Darwin"
is_linux_bundle = getattr(sys, "frozen", False) and platform.system() == "Linux"
