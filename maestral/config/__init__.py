import os

from .base import get_conf_path
from .main import MaestralConfig, MaestralState, migrate_user_config


def list_configs():
    """Lists all maestral configs"""
    configs = []
    for file in os.listdir(get_conf_path('maestral')):
        if file.endswith('.ini'):
            configs.append(os.path.splitext(os.path.basename(file))[0])

    return configs


for config_name in list_configs():
    migrate_user_config(config_name)
