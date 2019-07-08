# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

from maestral.client import MaestralApiClient
from maestral.main import Maestral
try:
    import PyQt5
    from maestral.gui.main import MaestralApp
except ImportError:
    print('Warning: PyQt5 is required to run the Maestral GUI. Run `pip install pyqt5` to install it.')