from .base import get_conf_path, list_configs
from .main import MaestralConfig, MaestralState
from .main import migrate_user_config as _migrate_user_config

for config_name in list_configs():
    _migrate_user_config(config_name)
