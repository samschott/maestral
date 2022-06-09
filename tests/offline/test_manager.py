import os
from unittest import mock

import pytest
from maestral.main import Maestral
from maestral.core import FullAccount, TeamRootInfo, UserRootInfo, AccountType
from maestral.exceptions import NoDropboxDirError
from maestral.utils.appdirs import get_home_dir
from maestral.utils.path import generate_cc_name, delete
from maestral.keyring import TokenType


def fake_linked(m: Maestral, account_info: FullAccount) -> None:
    m.client.get_account_info = mock.Mock(return_value=account_info)  # type: ignore
    m.cred_storage.save_creds("account_id", "1234", TokenType.Offline)


def verify_folder_structure(root: str, structure: dict) -> None:
    for name, children in structure.items():
        path = os.path.join(root, name)
        assert os.path.exists(path)

        verify_folder_structure(path, children)


def create_folder_structure(root: str, structure: dict) -> None:

    for name, children in structure.items():
        path = os.path.join(root, name)
        os.makedirs(path)

        create_folder_structure(path, children)


account_info = FullAccount(
    account_id="",
    display_name="",
    email="",
    profile_photo_url="",
    email_verified=False,
    disabled=False,
    country=None,
    locale="",
    team=None,
    team_member_id=None,
    account_type=AccountType.Business,
    root_info=UserRootInfo("", ""),
)


def test_migrate_path_root_user_to_team(m: Maestral) -> None:

    new_namespace_id = "2"
    home_path = "/John Doe"

    # patch client and sync engine

    account_info.root_info = TeamRootInfo(
        root_namespace_id=new_namespace_id,
        home_namespace_id="1",
        home_path=home_path,
    )

    fake_linked(m, account_info)

    home = get_home_dir()
    local_dropbox_dir = generate_cc_name(home + "/Dropbox", suffix="test runner")
    os.makedirs(local_dropbox_dir)

    try:
        m.sync.dropbox_path = local_dropbox_dir

        m.set_state("account", "path_root_type", "user")
        m.set_state("account", "path_root_nsid", "1")
        m.set_state("account", "home_path", "")

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
        assert m.get_state("account", "home_path") == home_path

    finally:
        delete(local_dropbox_dir)


def test_migrate_path_root_team_to_user(m: Maestral) -> None:

    new_namespace_id = "1"

    # patch client and sync engine

    account_info.root_info = UserRootInfo(
        root_namespace_id=new_namespace_id,
        home_namespace_id=new_namespace_id,
    )

    fake_linked(m, account_info)

    home = get_home_dir()
    local_dropbox_dir = generate_cc_name(home + "/Dropbox", suffix="test runner")
    os.makedirs(local_dropbox_dir)

    try:
        m.sync.dropbox_path = local_dropbox_dir

        m.set_state("account", "path_root_type", "team")
        m.set_state("account", "path_root_nsid", "2")
        m.set_state("account", "home_path", "/John Doe")

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
        assert m.get_state("account", "home_path") == ""

    finally:
        delete(local_dropbox_dir)


def test_migrate_path_root_team_to_team(m: Maestral) -> None:

    new_namespace_id = "3"

    # patch client and sync engine

    account_info.root_info = TeamRootInfo(
        root_namespace_id=new_namespace_id,
        home_namespace_id="1",
        home_path="/John Doe",
    )

    fake_linked(m, account_info)

    home = get_home_dir()
    local_dropbox_dir = generate_cc_name(home + "/Dropbox", suffix="test runner")
    os.makedirs(local_dropbox_dir)

    try:
        m.sync.dropbox_path = local_dropbox_dir

        m.set_state("account", "path_root_type", "team")
        m.set_state("account", "path_root_nsid", "2")
        m.set_state("account", "home_path", "/John Doe")

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
        assert m.get_state("account", "home_path") == "/John Doe"

    finally:
        delete(local_dropbox_dir)


def test_migrate_path_root_error(m: Maestral) -> None:

    new_namespace_id = "2"
    home_path = "/John Doe"

    # patch client and sync engine

    account_info.root_info = TeamRootInfo(
        root_namespace_id=new_namespace_id,
        home_namespace_id="1",
        home_path=home_path,
    )

    fake_linked(m, account_info)

    home = get_home_dir()
    local_dropbox_dir = generate_cc_name(home + "/Dropbox", suffix="test runner")

    m.sync.dropbox_path = local_dropbox_dir

    m.set_state("account", "path_root_type", "user")
    m.set_state("account", "path_root_nsid", "1")
    m.set_state("account", "home_path", "")

    # attempt to migrate folder structure without Dropbox dir

    with pytest.raises(NoDropboxDirError):
        m.manager.check_and_update_path_root()
