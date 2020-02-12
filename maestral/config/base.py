
import platform
import os
import os.path as osp


def get_home_dir():
    """
    Returns user home directory. This will be determined from the first
    valid result out of (osp.expanduser("~"), $HOME, $USERPROFILE, $TMP).
    """
    try:
        # expanduser() returns a raw byte string which needs to be
        # decoded with the codec that the OS is using to represent
        # file paths.
        path = osp.expanduser("~")
    except Exception:
        path = ''

    if osp.isdir(path):
        return path
    else:
        # Get home from alternative locations
        for env_var in ("HOME", "USERPROFILE", "TMP"):
            # os.environ.get() returns a raw byte string which needs to be
            # decoded with the codec that the OS is using to represent
            # environment variables.
            path = os.environ.get(env_var, '')
            if osp.isdir(path):
                return path
            else:
                path = ''

        if not path:
            raise RuntimeError("Please set the environment variable HOME to "
                               "your user/home directory.")


def get_conf_path(subfolder=None, filename=None, create=True):
    """
    Returns the default config path for the platform. This will be:

        - macOS: "~/Library/Application Support/<subfolder>/<filename>."
        - Linux: "XDG_CONFIG_HOME/<subfolder>/<filename>"
        - other: "~/.config/<subfolder>/<filename>"

    :param str subfolder: The subfolder for the app.
    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder "<subfolder>" will be created on-demand.
    """
    if platform.system() == "Darwin":
        conf_path = osp.join(get_home_dir(), "Library", "Application Support")
    else:
        fallback = osp.join(get_home_dir(), ".config")
        conf_path = os.environ.get("XDG_CONFIG_HOME", fallback)

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


def get_state_path(subfolder=None, filename=None, create=True):
    """
    Returns the default path to save application states for the platform. This will be:

        - macOS: "~/Library/Application Support/SUBFOLDER/FILENAME"
        - Linux: "$XDG_DATA_DIR/SUBFOLDER/FILENAME"
        - fallback: "$HOME/.local/share/SUBFOLDER/FILENAME"

    Note: We do not use "~/Library/Saved Application State" on macOS since this folder is
    reserved for user interface state and can be cleared by the user / system.

    :param str subfolder: The subfolder for the app.
    :param str filename: The filename to append for the app.
    :param bool create: If ``True``, the folder "<subfolder>" will be created on-demand.
    """

    # if-defs for different platforms
    if platform.system() == "Darwin":
        state_path = osp.join(get_home_dir(), "Library", "Application Support")
    else:
        fallback = osp.join(get_home_dir(), ".local", "share")
        state_path = os.environ.get("$XDG_DATA_DIR", fallback)

    # attach subfolder
    if subfolder:
        state_path = osp.join(state_path, subfolder)

    # create dir
    if create:
        os.makedirs(state_path, exist_ok=True)

    # attach filename
    if filename:
        state_path = osp.join(state_path, filename)

    return state_path


def list_configs():
    """Lists all maestral configs"""
    configs = []
    for file in os.listdir(get_conf_path('maestral')):
        if file.endswith('.ini'):
            configs.append(os.path.splitext(os.path.basename(file))[0])

    return configs
