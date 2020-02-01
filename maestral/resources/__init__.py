# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import os.path as osp

_root = getattr(sys, '_MEIPASS', osp.dirname(osp.abspath(__file__)))

APP_ICON_PATH = osp.join(_root, 'maestral.png')
