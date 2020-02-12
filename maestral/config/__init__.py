
from .base import migrate_user_config, list_configs
from .main import MaestralConfig, MaestralState


for config_name in list_configs():
    migrate_user_config(config_name)
