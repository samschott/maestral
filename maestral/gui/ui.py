# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

from PyQt5 import QtWidgets

THEME_DARK = "dark"
THEME_LIGHT = "light"


def _luminance(r, g, b, base=256):
    """
    Calculates luminance of a color, on a scale from 0 to 1, meaning that 1 is the
    highest luminance. r, g, b arguments values should be in 0..256 limits, or base
    argument should define the upper limit otherwise
    """
    return (0.2126*r + 0.7152*g + 0.0722*b)/base


def __pixel_at(x, y):
    """
    Returns (r, g, b) color code for a pixel with given coordinates (each value is in
    0..256 limits)
    """
    desktop_id = QtWidgets.QApplication.desktop().winId()
    screen = QtWidgets.QApplication.primaryScreen()
    color = screen.grabWindow(desktop_id, x, y, 1, 1).toImage().pixel(0, 0)
    return ((color >> 16) & 0xff), ((color >> 8) & 0xff), (color & 0xff)


def _get_theme():
    """
    Returns one of THEME_LIGHT or THEME_DARK, corresponding to current user's UI theme
    """
    # getting color of a pixel on a top bar, and identifying best-fitting color
    # theme based on its luminance
    pixel_rgb = __pixel_at(2, 2)
    luminance = _luminance(*pixel_rgb)
    return THEME_LIGHT if luminance >= 0.5 else THEME_DARK


THEME = _get_theme()
