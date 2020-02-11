"""
Base configuration management
"""

import os
import os.path as osp
import shutil
import logging

from maestral.utils.appdirs import get_conf_path

logger = logging.getLogger(__name__)


# =============================================================================
# Reset config files
# =============================================================================

def reset_config_files(subfolder, saved_config_files):
    """Remove all config files"""
    logger.info("*** Reset settings to defaults ***")
    for fname in saved_config_files:
        cfg_fname = get_conf_path(subfolder, fname)
        if osp.isfile(cfg_fname) or osp.islink(cfg_fname):
            os.remove(cfg_fname)
        elif osp.isdir(cfg_fname):
            shutil.rmtree(cfg_fname)
        else:
            continue
        logger.debug(f"removing: {cfg_fname}")
