import platform
import os
import os.path as osp
from maestral.config.base import get_home_dir, get_conf_path


def get_log_path(subfolder=None, filename=None, create=True):
    """Return absolute path to the log file with the specified filename."""
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
    if platform.system() == 'Darwin':
        return get_conf_path(subfolder, filename, create)
    else:
        return get_log_path(subfolder, filename, create)
