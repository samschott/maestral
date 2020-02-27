from .base import get_conf_path, list_configs
from .main import MaestralConfig, MaestralState
from maestral.utils.housekeeping import migrate_user_config


__all__ = ["get_conf_path", "list_configs", "MaestralConfig", "MaestralState"]


for config_name in list_configs():
    migrate_user_config(config_name)
