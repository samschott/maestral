"""
Base configuration management

This file only deals with non-GUI configuration features
(in other words, we won't import any PyQt object here, avoiding any
sip API incompatibility issue in spyder's non-gui modules)
"""

from __future__ import division, absolute_import
import sys
import os
import os.path as osp
import shutil


STDERR = sys.stderr


# =============================================================================
# Configuration paths
# =============================================================================

def get_home_dir():
    """
    Return user home directory
    """
    try:
        # expanduser() returns a raw byte string which needs to be
        # decoded with the codec that the OS is using to represent
        # file paths.
        path = osp.expanduser('~')
    except Exception:
        path = ''

    if osp.isdir(path):
        return path
    else:
        # Get home from alternative locations
        for env_var in ('HOME', 'USERPROFILE', 'TMP'):
            # os.environ.get() returns a raw byte string which needs to be
            # decoded with the codec that the OS is using to represent
            # environment variables.
            path = os.environ.get(env_var, '')
            if osp.isdir(path):
                return path
            else:
                path = ''

        if not path:
            raise RuntimeError('Please set the environment variable HOME to '
                               'your user/home directory.')


def get_conf_path(subfolder=None, filename=None):
    """Return absolute path to the config file with the specified filename."""
    # Define conf_dir
    conf_dir = osp.join(get_home_dir(), subfolder)

    # Create conf_dir
    if not osp.isdir(conf_dir):
        os.mkdir(conf_dir)
    if filename is None:
        return conf_dir
    else:
        return osp.join(conf_dir, filename)


# =============================================================================
# Reset config files
# =============================================================================

def reset_config_files(subfolder, saved_config_files):
    """Remove all config files"""
    print("*** Reset settings to defaults ***", file=STDERR)
    for fname in saved_config_files:
        cfg_fname = get_conf_path(subfolder, fname)
        if osp.isfile(cfg_fname) or osp.islink(cfg_fname):
            os.remove(cfg_fname)
        elif osp.isdir(cfg_fname):
            shutil.rmtree(cfg_fname)
        else:
            continue
        print("removing:", cfg_fname, file=STDERR)
