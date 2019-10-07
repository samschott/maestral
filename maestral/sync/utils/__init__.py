# -*- coding: utf-8 -*-

import sys
import os
import shutil
import platform
import re
from distutils.version import LooseVersion

is_macos_bundle = getattr(sys, "frozen", False) and platform.system() == "Darwin"
is_linux_bundle = getattr(sys, "frozen", False) and platform.system() == "Linux"


def is_stable_version(version):
    """
    Return true if version is stable, i.e. with letters in the final component.

    Stable version examples: ``1.2``, ``1.3.4``, ``1.0.5``.
    Non-stable version examples: ``1.3.4beta``, ``0.1.0rc1``, ``3.0.0dev0``.
    """
    if not isinstance(version, tuple):
        version = version.split(".")
    last_part = version[-1]

    if not re.search(r"[a-zA-Z]", last_part):
        return True
    else:
        return False


def check_version(actver, version, cmp_op):
    """
    Check version string of an active module against a required version.

    If dev/prerelease tags result in TypeError for string-number comparison,
    it is assumed that the dependency is satisfied.
    Users on dev branches are responsible for keeping their own packages up to
    date.

    Copyright (C) 2013  The IPython Development Team

    Distributed under the terms of the BSD License.
    """
    if isinstance(actver, tuple):
        actver = '.'.join([str(i) for i in actver])

    # Hacks needed so that LooseVersion understands that (for example)
    # version = '3.0.0' is in fact bigger than actver = '3.0.0rc1'
    if is_stable_version(version) and not is_stable_version(actver) and \
      actver.startswith(version) and version != actver:
        version = version + 'zz'
    elif is_stable_version(actver) and not is_stable_version(version) and \
      version.startswith(actver) and version != actver:
        actver = actver + 'zz'

    try:
        if cmp_op == '>':
            return LooseVersion(actver) > LooseVersion(version)
        elif cmp_op == '>=':
            return LooseVersion(actver) >= LooseVersion(version)
        elif cmp_op == '=':
            return LooseVersion(actver) == LooseVersion(version)
        elif cmp_op == '<':
            return LooseVersion(actver) < LooseVersion(version)
        elif cmp_op == '<=':
            return LooseVersion(actver) <= LooseVersion(version)
        else:
            return False
    except TypeError:
        return True


def delete_file_or_folder(path, return_error=False):
    """
    Deletes a file or folder at :param:`path`. Returns True on success,
    False otherwise.
    """
    success = True
    err = None

    try:
        shutil.rmtree(path)
    except OSError:
        try:
            os.unlink(path)
        except OSError as e:
            success = False
            err = e

    if return_error:
        return success, err
    else:
        return success

