import platform
import os
import os.path as osp
import logging

from maestral.config.base import get_home_dir, get_conf_path

logger = logging.getLogger(__name__)


def get_log_path(subfolder=None, filename=None, create=True):
    """
    Returns the default log path for the platform. This will be:

        - macOS: '~/Library/Logs/SUBFOLDER/FILENAME'
        - Linux: '$XDG_CACHE_HOME/SUBFOLDER/FILENAME'
        - fallback: '~/.cache/SUBFOLDER/FILENAME'

    :param str subfolder: The subfolder for the app.
    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder '<subfolder>' will be created on-demand.
    """
    # check if there is a XDG default
    xdg_cache_home = os.environ.get('XDG_CACHE_HOME', '')
    # if-defs for different platforms
    if platform.system() == 'Linux' and xdg_cache_home:
        log_dir = osp.join(xdg_cache_home, subfolder)
    elif platform.system() == 'Darwin':
        log_dir = osp.join(get_home_dir(), 'Library', 'Logs', subfolder)
    else:
        log_dir = osp.join(get_home_dir(), '.cache', subfolder)

    # create log_dir
    if create and not osp.isdir(log_dir):
        os.makedirs(log_dir)

    # return runtime_dir (+ filename)
    if filename is None:
        return log_dir
    else:
        return osp.join(log_dir, filename)


def get_cache_path(subfolder=None, filename=None, create=True):
    """
    Returns the default cache path for the platform. This will be:

        - macOS: '~/Library/Application Support/SUBFOLDER/FILENAME'
        - Linux: '$XDG_CACHE_HOME/SUBFOLDER/FILENAME'
        - fallback: '~/.cache/SUBFOLDER/FILENAME'

    :param str subfolder: The subfolder for the app.
    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder '<subfolder>' will be created on-demand.
    """
    if platform.system() == 'Darwin':
        return get_conf_path(subfolder, filename, create)
    else:
        return get_log_path(subfolder, filename, create)


def get_autostart_path(filename=None, create=True):
    """
    Returns the default cache path for the platform. This will be:

        - macOS: '~/Library/LaunchAgents/FILENAME'
        - Linux: '$XDG_CONFIG_HOME/autostart/FILENAME'
        - fallback: '~/.config/autostart/FILENAME'

    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder '<subfolder>' will be created on-demand.
    """
    if platform.system() == 'Darwin':
        return osp.join(get_home_dir(), "Library", "LaunchAgents", filename)
    else:
        return get_log_path("autostart", filename, create)


def get_runtime_path(subfolder=None, filename=None, create=True):
    """
    Returns the default runtime directory for the platform. This will be:

        - macOS: '~/Library/Application Support/SUBFOLDER/FILENAME'
        - Linux: '$XDG_RUNTIME_DIR/SUBFOLDER/FILENAME'
        - fallback: '~/.cache/SUBFOLDER/FILENAME'

    :param str subfolder: The subfolder for the app.
    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder '<subfolder>' will be created on-demand.
    """
    # check if there is a XDG default
    xdg_runtime_dir = os.environ.get('XDG_RUNTIME_DIR', '')
    # if-defs for different platforms
    if platform.system() == 'Linux' and xdg_runtime_dir:
        runtime_dir = osp.join(xdg_runtime_dir, subfolder)
    else:
        runtime_dir = get_cache_path(subfolder, filename)
        logger.warning("$XDG_RUNTIME_DIR is not set. '"
                       "'Using '{}' instead.".format(runtime_dir))

    # create runtime_dir
    if create and not osp.isdir(runtime_dir):
        os.makedirs(runtime_dir)

    # return runtime_dir (+ filename)
    if filename is None:
        return runtime_dir
    else:
        return osp.join(runtime_dir, filename)
