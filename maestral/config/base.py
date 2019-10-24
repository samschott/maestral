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
import platform

STDERR = sys.stderr


# =============================================================================
# Configuration paths
# =============================================================================

def get_home_dir():
    """
    Returns user home directory. This will be determined from the first
    valid result out of (osp.expanduser('~'), $HOME, $USERPROFILE, $TMP).
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


def get_conf_path(subfolder=None, filename=None, create=True):
    """
    Returns the default config path for the platform. This will be:

        - macOS: '~/Library/Application Support/<subfolder>/<filename>.'
        - Linux: 'XDG_CONFIG_HOME/<subfolder>/<filename>'
        - other: '~/.config/<subfolder>/<filename>'

    :param str subfolder: The subfolder for the app.
    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder '<subfolder>' will be created on-demand.
    """
    if platform.system() == 'Darwin':
        conf_path = osp.join(get_home_dir(), 'Library', 'Application Support')
    else:
        fallback = osp.join(get_home_dir(), '.config')
        conf_path = os.environ.get('XDG_CONFIG_HOME', fallback)

    # attach subfolder
    if subfolder:
        conf_path = osp.join(conf_path, subfolder)

    # create dir
    if create:
        os.makedirs(conf_path, exist_ok=True)

    # attach filename
    if filename:
        conf_path = osp.join(conf_path, filename)

    return conf_path


def get_old_conf_path(subfolder=None, filename=None):
    """Return absolute path to the config file with the specified filename."""
    # Define conf_dir
    conf_dir = osp.join(get_home_dir(), subfolder)

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


# =============================================================================
# Migrate config files
# =============================================================================

def migrate_config_files():
    """
    Code to migrate from old config file locations to new locations. Config files will
    be stored in '$XDG_CONFIG_HOME/maestral' in Linux (or '~/.config/maestral' if
    $XDG_CONFIG_HOME is not set) and in '~/Library/Application Support/maestral' on macOS.
    """
    import os
    import shutil

    old_path = get_old_conf_path('.maestral')
    new_path = get_conf_path('maestral', create=False)

    if os.path.isdir(old_path):
        try:
            shutil.copytree(old_path, new_path)
        except FileExistsError:
            print("New config at '{}' already exists.".format(new_path))

        try:
            shutil.rmtree(old_path)
        except OSError:
            print("Could not remove old config at '{}'.".format(old_path))

        print("Migrated config files from '{}' to '{}'.".format(old_path, new_path))
