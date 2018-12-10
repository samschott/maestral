# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

from .client import MaestralClient
from .main import Maestral
try:
    import PyQt5
    from .gui.main import MaestralApp
except ImportError:
    print('Warning: PyQt5 is required to run the Maestral GUI. Run `pip install pyqt5` to install it.')