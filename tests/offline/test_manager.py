import os
from typing import Dict

import pytest
from dropbox.users import FullAccount
from dropbox.common import TeamRootInfo, UserRootInfo

from maestral.errors import NoDropboxDirError
from maestral.utils.appdirs import get_home_dir
from maestral.utils.path import generate_cc_name, delete


def verify_folder_structure(root: str, structure: Dict[str, Dict]) -> None:
    for name, children in structure.items():
        path = os.path.join(root, name)
        assert os.path.exists(path)

        verify_folder_structure(path, children)


def create_folder_structure(root: str, structure: Dict[str, Dict]) -> None:

    for name, children in structure.items():
        path = os.path.join(root, name)
        os.makedirs(path)

        create_folder_structure(path, children)


def test_migrate_path_root_user_to_team(m):

    new_namespace_id = "2"
    home_path = "/John Doe"

    # patch client and sync engine

    def get_account_info() -> FullAccount:
        return FullAccount(
            root_info=TeamRootInfo(
                root_namespace_id=new_namespace_id,
                home_namespace_id="1",
                home_path=home_path,
            ),
        )

    def switch_path_root(nsid) -> None:
        m.set_state("account", "path_root_nsid", nsid)

    m.client.get_account_info = get_account_info
    m.client.switch_path_root = switch_path_root

    home = get_home_dir()
    local_dropbox_dir = generate_cc_name(home + "/Dropbox", suffix="test runner")
    os.makedirs(local_dropbox_dir)

    try:
        m.sync.dropbox_path = local_dropbox_dir

        m.set_state("account", "path_root_type", "user")
        m.set_state("account", "path_root_nsid", "1")
        m.set_state("account", "home_path_name", "")

        # define folder structures before and after migration

        dir_layout_old = {
            "Documents": {},
            "Photos": {
                "March 2019": {},
            },
            "John Doe": {},
            "Personal": {},
        }

        dir_layout_new = {
            "John Doe": {
                "Documents": {},
                "Photos": {
                    "March 2019": {},
                },
                "John Doe": {},
                "Personal": {},
            }
        }

        # create folder structure before migration

        create_folder_structure(local_dropbox_dir, dir_layout_old)

        # migrate folder structure and verify migration

        m.manager.check_and_update_path_root()

        verify_folder_structure(local_dropbox_dir, dir_layout_new)

        assert m.get_state("account", "path_root_type") == "team"
        assert m.get_state("account", "path_root_nsid") == new_namespace_id
        assert m.get_state("account", "home_path_name") == home_path

    finally:
        delete(local_dropbox_dir)


def test_migrate_path_root_team_to_user(m):

    new_namespace_id = "1"

    # patch client and sync engine

    def get_account_info() -> FullAccount:
        return FullAccount(
            root_info=UserRootInfo(
                root_namespace_id=new_namespace_id,
                home_namespace_id=new_namespace_id,
            ),
        )

    def switch_path_root(nsid) -> None:
        m.set_state("account", "path_root_nsid", nsid)

    m.client.get_account_info = get_account_info
    m.client.switch_path_root = switch_path_root

    home = get_home_dir()
    local_dropbox_dir = generate_cc_name(home + "/Dropbox", suffix="test runner")
    os.makedirs(local_dropbox_dir)

    try:
        m.sync.dropbox_path = local_dropbox_dir

        m.set_state("account", "path_root_type", "team")
        m.set_state("account", "path_root_nsid", "2")
        m.set_state("account", "home_path_name", "/John Doe")

        # define folder structures before and after migration

        dir_layout_old = {
            "John Doe": {
                "Documents": {},
                "Photos": {
                    "March 2019": {},
                },
                "John Doe": {},
                "Personal": {},
            },
            "Team folder 1": {},
            "Team folder 2": {
                "Subfolder": {},
            },
        }

        dir_layout_new = {
            "Documents": {},
            "Photos": {
                "March 2019": {},
            },
            "John Doe": {},
            "Personal": {},
        }

        # create folder structure before migration

        create_folder_structure(local_dropbox_dir, dir_layout_old)

        # migrate folder structure and verify migration

        m.manager.check_and_update_path_root()

        verify_folder_structure(local_dropbox_dir, dir_layout_new)

        assert m.get_state("account", "path_root_type") == "user"
        assert m.get_state("account", "path_root_nsid") == new_namespace_id
        assert m.get_state("account", "home_path_name") == ""

    finally:
        delete(local_dropbox_dir)


def test_migrate_path_root_team_to_team(m):

    new_namespace_id = "3"

    # patch client and sync engine

    def get_account_info() -> FullAccount:
        return FullAccount(
            root_info=TeamRootInfo(
                root_namespace_id=new_namespace_id,
                home_namespace_id="1",
                home_path="/John Doe",
            ),
        )

    def switch_path_root(nsid) -> None:
        m.set_state("account", "path_root_nsid", nsid)

    m.client.get_account_info = get_account_info
    m.client.switch_path_root = switch_path_root

    home = get_home_dir()
    local_dropbox_dir = generate_cc_name(home + "/Dropbox", suffix="test runner")
    os.makedirs(local_dropbox_dir)

    try:
        m.sync.dropbox_path = local_dropbox_dir

        m.set_state("account", "path_root_type", "team")
        m.set_state("account", "path_root_nsid", "2")
        m.set_state("account", "home_path_name", "/John Doe")

        # define folder structures before and after migration

        dir_layout_old = {
            "John Doe": {
                "Documents": {},
                "Photos": {
                    "March 2019": {},
                },
                "John Doe": {},
                "Personal": {},
            },
            "Team folder 1": {},
            "Team folder 2": {
                "Subfolder": {},
            },
        }

        dir_layout_new = {
            "John Doe": {
                "Documents": {},
                "Photos": {
                    "March 2019": {},
                },
                "John Doe": {},
                "Personal": {},
            },
        }

        # create folder structure before migration

        create_folder_structure(local_dropbox_dir, dir_layout_old)

        # migrate folder structure and verify migration

        m.manager.check_and_update_path_root()

        verify_folder_structure(local_dropbox_dir, dir_layout_new)

        assert m.get_state("account", "path_root_type") == "team"
        assert m.get_state("account", "path_root_nsid") == new_namespace_id
        assert m.get_state("account", "home_path_name") == "/John Doe"

    finally:
        delete(local_dropbox_dir)


def test_migrate_path_root_error(m):

    new_namespace_id = "2"
    home_path = "/John Doe"

    # patch client and sync engine

    def get_account_info() -> FullAccount:
        return FullAccount(
            root_info=TeamRootInfo(
                root_namespace_id=new_namespace_id,
                home_namespace_id="1",
                home_path=home_path,
            ),
        )

    def switch_path_root(nsid) -> None:
        m.set_state("account", "path_root_nsid", nsid)

    m.client.get_account_info = get_account_info
    m.client.switch_path_root = switch_path_root

    home = get_home_dir()
    local_dropbox_dir = generate_cc_name(home + "/Dropbox", suffix="test runner")

    m.sync.dropbox_path = local_dropbox_dir

    m.set_state("account", "path_root_type", "user")
    m.set_state("account", "path_root_nsid", "1")
    m.set_state("account", "home_path_name", "")

    # attempt to migrate folder structure without Dropbox dir

    with pytest.raises(NoDropboxDirError):
        m.manager.check_and_update_path_root()
