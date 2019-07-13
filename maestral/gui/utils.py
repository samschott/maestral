# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

from PyQt5 import QtGui, QtWidgets


def truncate_string(string, font=None, pixels=200, side="right"):

    if side == "right":
        return _truncate_string_right(string, font=font, pixels=pixels)
    elif side == "left":
        return _truncate_string_left(string, font=font, pixels=pixels)


def _truncate_string_right(string, font=None, pixels=200):
    """
    Truncates strings so that it is short than `pixels` in the given `font`.

    :param str string: String to truncate.
    :param font: QFont used to determine the pixel width of the text.
    :param int pixels: Maximum allowed width in pixels.

    :return: Truncated string.
    :rtype: str
    """

    if not font:
        test_label = QtWidgets.QLabel()
        font = test_label.font()

    metrics = QtGui.QFontMetrics(font)

    truncated = False
    new_string = string

    # truncate string using the average width per character
    if metrics.width(string) > pixels:
        pixel_per_char = metrics.width(string) / len(string)
        cutoff = int(pixels / pixel_per_char)
        new_string = string[0:cutoff]
        truncated = True

        # truncate further if necessary
        while metrics.width(new_string) > pixels:
            new_string = new_string[0:-1]

        # expand if truncated too far
        while metrics.width(new_string) < pixels:
            cutoff = len(new_string)
            new_string = new_string + string[cutoff:cutoff + 1]

    return new_string + ('...' if truncated else '')


def _truncate_string_left(string, font=None, pixels=300):
    """
    Truncates strings so that it is short than `pixels` in the given `font`.

    :param str string: String to truncate.
    :param int pixels: Maximum allowed width in pixels.

    :return: Truncated string.
    :rtype: str
    """
    if not font:
        test_label = QtWidgets.QLabel()
        font = test_label.font()
    metrics = QtGui.QFontMetrics(font)

    truncated = False
    new_string = string

    # truncate string using the average width per character
    if metrics.width(string) > pixels:
        pixel_per_char = metrics.width(string) / len(string)
        cutoff = int(pixels / pixel_per_char)
        new_string = string[cutoff:]
        truncated = True

        # truncate further if necessary
        while metrics.width(new_string) > pixels:
            new_string = new_string[1:]

        # expand if truncated too far
        while metrics.width(new_string) < pixels:
            cutoff = len(new_string)
            new_string = string[-cutoff:-cutoff+1] + new_string

    return ('...' if truncated else '') + new_string
