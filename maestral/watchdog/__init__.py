# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
import platform

if platform.system() == "Darwin":
    from .fsevents import OrderedFSEventsObserver as Observer
else:
    from watchdog.observers import Observer

__all__ = ["Observer"]
