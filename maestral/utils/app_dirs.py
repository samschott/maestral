import platform
import os
import os.path as osp
from maestral.config.base import get_home_dir, get_conf_path


def get_log_path(subfolder=None, filename=None, create=True):
    """
    Returns the default log path for the platform. This will be:

        - macOS: '~/Library/Logs/SUBFOLDER/FILENAME'
        - Linux: 'XDG_CACHE_HOME/SUBFOLDER/FILENAME'
        - other: '~/.cache/SUBFOLDER/FILENAME'

    :param str subfolder: The subfolder for the app.
    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder '<subfolder>' will be created on-demand.
    """

    # Define log_dir
    if platform.system() == 'Linux':
        # This makes us follow the XDG standard to save our settings
        # on Linux
        xdg_cache_home = os.environ.get('XDG_CACHE_HOME', '')
        if not xdg_cache_home:
            xdg_cache_home = osp.join(get_home_dir(), '.cache')

        if create and not osp.isdir(xdg_cache_home):
            os.makedirs(xdg_cache_home)

        log_dir = osp.join(xdg_cache_home, subfolder)
    elif platform.system() == 'Darwin':
        log_dir = osp.join(get_home_dir(), 'Library', 'Logs', subfolder)
    else:
        log_dir = osp.join(get_home_dir(), '.cache', subfolder)

    # Create conf_dir
    if create and not osp.isdir(log_dir):
        os.mkdir(log_dir)
    if filename is None:
        return log_dir
    else:
        return osp.join(log_dir, filename)


def get_cache_path(subfolder=None, filename=None, create=True):
    """
    Returns the default cache path for the platform. This will be:

        - macOS: '~/Library/Application Support/SUBFOLDER/FILENAME'
        - Linux: 'XDG_CACHE_HOME/SUBFOLDER/FILENAME'
        - other: '~/.cache/SUBFOLDER/FILENAME'

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
        - Linux: 'XDG_CONFIG_HOME/autostart/FILENAME'
        - other: '~/.config/autostart/FILENAME'

    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder '<subfolder>' will be created on-demand.
    """
    if platform.system() == 'Darwin':
        return osp.join(get_home_dir(), "Library", "LaunchAgents", filename)
    else:
        return get_log_path("autostart", filename, create)
