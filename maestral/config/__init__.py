import os

for config_name in list_configs():
    migrate_user_config(config_name)
from .base import get_conf_path, list_configs
from .main import MaestralConfig, MaestralState
