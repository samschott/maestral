# -*- coding: utf-8 -*-

import sys
import os
import platform

is_macos_bundle = getattr(sys, "frozen", False) and platform.system() == "Darwin"
is_linux_bundle = getattr(sys, "frozen", False) and platform.system() == "Linux"


def get_desktop():
    """
    Determines the current desktop environment. This is used for instance to decide
    which keyring backend is preferred to store the auth token.

    :returns: "gnome", "kde", "xfce", "cocoa", "" or any other string if the desktop
        $XDG_CURRENT_DESKTOP if the desktop environment is not known to us.
    :rtype: str
    """

    if platform.system() == "Linux":
        current_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        desktop_session = os.environ.get("GDMSESSION", "").lower()

        for desktop in ("gnome", "kde", "xfce", ""):
            if desktop in current_desktop or desktop in desktop_session:
                return desktop

        return current_desktop

    elif platform.system() == "Darwin":
        return "cocoa"
