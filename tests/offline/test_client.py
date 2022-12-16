from datetime import datetime
from datetime import timezone

import pytest
import requests
from unittest.mock import Mock
from dropbox.oauth import DropboxOAuth2FlowNoRedirect
from dropbox import users, users_common, common, team_common, files, sharing
from maestral.client import (
    DropboxClient,
    convert_account,
    convert_full_account,
    convert_space_usage,
    convert_metadata,
    convert_shared_link_metadata,
)
from maestral.keyring import CredentialStorage
from maestral import core
from maestral.exceptions import NotLinkedError


# ==== DropboxClient tests =============================================================


def test_get_auth_url():
    cred_storage = CredentialStorage("test-config")
    client = DropboxClient("test-config", cred_storage)
    assert client.get_auth_url().startswith("https://")


def test_link():
    cred_storage = Mock(spec_set=CredentialStorage)
    client = DropboxClient("test-config", cred_storage)

    client._auth_flow = Mock(spec_set=DropboxOAuth2FlowNoRedirect)
    client.get_account_info = Mock()
    client.update_path_root = Mock()

    res = client.link("code")

    assert res == 0
    client.update_path_root.assert_called_once()
    cred_storage.save_creds.assert_called_once()


def test_link_error():
    cred_storage = CredentialStorage("test-config")
    client = DropboxClient("test-config", cred_storage)

    with pytest.raises(RuntimeError):
        client.link("code")


def test_link_failed_1():
    cred_storage = CredentialStorage("test-config")
    client = DropboxClient("test-config", cred_storage)

    client._auth_flow = Mock(spec_set=DropboxOAuth2FlowNoRedirect)
    client._auth_flow.finish = Mock(side_effect=requests.exceptions.HTTPError("failed"))

    res = client.link("token")

    assert res == 1


def test_link_failed_2():
    cred_storage = Mock(spec_set=CredentialStorage)
    client = DropboxClient("test-config", cred_storage)

    client._auth_flow = Mock(spec_set=DropboxOAuth2FlowNoRedirect)
    client._auth_flow.finish = Mock(side_effect=ConnectionError("failed"))

    res = client.link("token")

    assert res == 2

    client._auth_flow = Mock(spec_set=DropboxOAuth2FlowNoRedirect)
    client.get_account_info = Mock()
    client.update_path_root = Mock(side_effect=ConnectionError("failed"))

    res = client.link("token")

    assert res == 2


def test_unlink_error():
    cred_storage = CredentialStorage("test-config")
    client = DropboxClient("test-config", cred_storage)

    with pytest.raises(NotLinkedError):
        client.unlink()


# ==== type conversion tests ===========================================================


def test_convert_account():

    dbx_account_info = users.Account(
        account_id="1234" * 10,
        name=users.Name(
            given_name="1",
            surname="2",
            display_name="3",
            abbreviated_name="4",
            familiar_name="5",
        ),
        email="mail@musterman.com",
        email_verified=True,
        profile_photo_url="url",
        disabled=False,
    )

    account_info = convert_account(dbx_account_info)

    assert isinstance(account_info, core.Account)
    assert account_info.account_id == "1234" * 10
    assert account_info.display_name == "3"
    assert account_info.email == "mail@musterman.com"
    assert account_info.email_verified is True
    assert account_info.profile_photo_url == "url"


def test_convert_full_account():

    dbx_account_info = users.FullAccount(
        account_id="1234" * 10,
        name=users.Name(
            given_name="1",
            surname="2",
            display_name="3",
            abbreviated_name="4",
            familiar_name="5",
        ),
        email="mail@musterman.com",
        email_verified=True,
        profile_photo_url="url",
        disabled=False,
        country="UK",
        locale="EN_GB",
        team=None,
        team_member_id=None,
        is_paired=False,
        account_type=users_common.AccountType.basic,
        root_info=common.UserRootInfo(
            root_namespace_id="root_id", home_namespace_id="home_id"
        ),
    )

    account_info = convert_full_account(dbx_account_info)

    assert isinstance(account_info, core.FullAccount)
    assert account_info.account_id == "1234" * 10
    assert account_info.display_name == "3"
    assert account_info.email == "mail@musterman.com"
    assert account_info.email_verified is True
    assert account_info.profile_photo_url == "url"
    assert account_info.country == "UK"
    assert account_info.locale == "EN_GB"
    assert account_info.team is None
    assert account_info.team_member_id is None
    assert account_info.account_type is core.AccountType.Basic
    assert account_info.root_info == core.UserRootInfo(
        root_namespace_id="root_id", home_namespace_id="home_id"
    )

    dbx_account_info.account_type = users_common.AccountType.pro

    account_info = convert_full_account(dbx_account_info)

    assert account_info.account_type is core.AccountType.Pro
    assert account_info.root_info == core.UserRootInfo(
        root_namespace_id="root_id", home_namespace_id="home_id"
    )

    dbx_account_info.account_type = users_common.AccountType.business
    dbx_account_info.root_info = common.TeamRootInfo(
        root_namespace_id="root_id", home_namespace_id="home_id", home_path="/home"
    )

    account_info = convert_full_account(dbx_account_info)

    assert account_info.account_type is core.AccountType.Business
    assert account_info.root_info == core.TeamRootInfo(
        root_namespace_id="root_id", home_namespace_id="home_id", home_path="/home"
    )


def test_convert_space_usage_individual():
    dbx_space_usage = users.SpaceUsage(
        used=10,
        allocation=users.SpaceAllocation.individual(
            users.IndividualSpaceAllocation(allocated=20)
        ),
    )

    space_usage = convert_space_usage(dbx_space_usage)

    assert isinstance(space_usage, core.SpaceUsage)
    assert space_usage.used == 10
    assert space_usage.allocated == 20
    assert space_usage.team_usage is None


def test_convert_space_usage_team():
    dbx_space_usage = users.SpaceUsage(
        used=10,
        allocation=users.SpaceAllocation.team(
            users.TeamSpaceAllocation(
                used=20,
                allocated=30,
                user_within_team_space_allocated=0,
                user_within_team_space_limit_type=team_common.MemberSpaceLimitType.alert_only,
            )
        ),
    )

    space_usage = convert_space_usage(dbx_space_usage)

    assert isinstance(space_usage, core.SpaceUsage)
    assert space_usage.used == 10
    assert space_usage.allocated == 30
    assert space_usage.team_usage == core.TeamSpaceUsage(20, 30)

    dbx_space_usage = users.SpaceUsage(
        used=10,
        allocation=users.SpaceAllocation.team(
            users.TeamSpaceAllocation(
                used=20,
                allocated=30,
                user_within_team_space_allocated=15,
                user_within_team_space_limit_type=team_common.MemberSpaceLimitType.alert_only,
            )
        ),
    )

    space_usage = convert_space_usage(dbx_space_usage)

    assert isinstance(space_usage, core.SpaceUsage)
    assert space_usage.used == 10
    assert space_usage.allocated == 15
    assert space_usage.team_usage == core.TeamSpaceUsage(20, 30)


def test_convert_space_usage_other():
    dbx_space_usage = users.SpaceUsage(
        used=10,
        allocation=users.SpaceAllocation.other,
    )

    space_usage = convert_space_usage(dbx_space_usage)

    assert isinstance(space_usage, core.SpaceUsage)
    assert space_usage.used == 10
    assert space_usage.allocated == 0
    assert space_usage.team_usage is None


def test_convert_metadata_file():

    dbx_md = files.FileMetadata(
        name="Hello",
        path_lower="/folder/hello",
        path_display="/folder/Hello",
        id="id-0123456789",
        client_modified=datetime.utcfromtimestamp(10),
        server_modified=datetime.utcfromtimestamp(20),
        rev="abcdf12687980",
        size=658,
        symlink_info=files.SymlinkInfo(target="/symlink-target"),
        sharing_info=files.FileSharingInfo(
            read_only=False,
            parent_shared_folder_id="parent_shared_folder_id",
            modified_by="dbid-kjahdskjhkljkadsjhjhjmwerjhjhjmwero",
        ),
        is_downloadable=True,
        content_hash="content_hash_hjkglidjsadfjhsdfgkasdhfgocapigkasdhfgociuyoweruqpi",
    )

    md = convert_metadata(dbx_md)

    assert isinstance(md, core.FileMetadata)
    assert md.name == "Hello"
    assert md.path_display == "/folder/Hello"
    assert md.path_lower == "/folder/hello"
    assert md.id == "id-0123456789"
    assert md.client_modified == datetime.fromtimestamp(10, tz=timezone.utc)
    assert md.server_modified == datetime.fromtimestamp(20, tz=timezone.utc)
    assert md.rev == "abcdf12687980"
    assert md.size == 658
    assert md.symlink_target == "/symlink-target"
    assert md.is_downloadable is True
    assert (
        md.content_hash
        == "content_hash_hjkglidjsadfjhsdfgkasdhfgocapigkasdhfgociuyoweruqpi"
    )
    assert md.is_downloadable is True
    assert md.shared is True


def test_convert_metadata_folder():

    dbx_md = files.FolderMetadata(
        name="Hello",
        path_lower="/folder/hello",
        path_display="/folder/Hello",
        id="id-0123456789",
        sharing_info=files.FolderSharingInfo(
            read_only=False,
            parent_shared_folder_id="parent_shared_folder_id",
        ),
    )

    md = convert_metadata(dbx_md)

    assert isinstance(md, core.FolderMetadata)
    assert md.name == "Hello"
    assert md.path_display == "/folder/Hello"
    assert md.path_lower == "/folder/hello"
    assert md.id == "id-0123456789"
    assert md.shared is True


def test_convert_metadata_deleted():

    dbx_md = files.DeletedMetadata(
        name="Hello",
        path_lower="/folder/hello",
        path_display="/folder/Hello",
    )

    md = convert_metadata(dbx_md)

    assert isinstance(md, core.DeletedMetadata)
    assert md.name == "Hello"
    assert md.path_display == "/folder/Hello"
    assert md.path_lower == "/folder/hello"


def test_convert_metadata_unsupported():

    dbx_md = files.Metadata(
        name="Hello",
        path_lower="/folder/hello",
        path_display="/folder/Hello",
    )

    with pytest.raises(RuntimeError):
        convert_metadata(dbx_md)


def test_convert_sharedlink_metdata():

    # Test conversion with effective_audience.

    dbx_md = sharing.SharedLinkMetadata(
        url="/url",
        name="Hello",
        path_lower="/folder/hello",
        expires=datetime.utcfromtimestamp(10),
        link_permissions=sharing.LinkPermissions(
            can_revoke=False,
            effective_audience=sharing.LinkAudience.public,
            link_access_level=sharing.LinkAccessLevel.viewer,
            require_password=True,
            allow_download=True,
        ),
    )

    md = convert_shared_link_metadata(dbx_md)

    assert isinstance(md, core.SharedLinkMetadata)
    assert md.url == "/url"
    assert md.name == "Hello"
    assert md.path_lower == "/folder/hello"
    assert md.expires == datetime.fromtimestamp(10, tz=timezone.utc)
    assert md.link_permissions.require_password is True
    assert md.link_permissions.can_revoke is False
    assert md.link_permissions.allow_download is True
    assert md.link_permissions.link_access_level is core.LinkAccessLevel.Viewer
    assert md.link_permissions.effective_audience is core.LinkAudience.Public

    dbx_md.link_permissions.effective_audience = sharing.LinkAudience.team

    md = convert_shared_link_metadata(dbx_md)

    assert md.link_permissions.effective_audience is core.LinkAudience.Team

    dbx_md.link_permissions.effective_audience = sharing.LinkAudience.no_one

    md = convert_shared_link_metadata(dbx_md)

    assert md.link_permissions.effective_audience is core.LinkAudience.NoOne

    # Test conversion with resolved_visibility.

    dbx_md = sharing.SharedLinkMetadata(
        url="/url",
        name="Hello",
        path_lower="/folder/hello",
        expires=datetime.utcfromtimestamp(10),
        link_permissions=sharing.LinkPermissions(
            can_revoke=False,
            resolved_visibility=sharing.ResolvedVisibility.public,
            link_access_level=sharing.LinkAccessLevel.editor,
            allow_download=True,
        ),
    )

    md = convert_shared_link_metadata(dbx_md)

    assert isinstance(md, core.SharedLinkMetadata)
    assert md.url == "/url"
    assert md.name == "Hello"
    assert md.path_lower == "/folder/hello"
    assert md.expires == datetime.fromtimestamp(10, tz=timezone.utc)
    assert md.link_permissions.require_password is False
    assert md.link_permissions.can_revoke is False
    assert md.link_permissions.allow_download is True
    assert md.link_permissions.link_access_level is core.LinkAccessLevel.Editor
    assert md.link_permissions.effective_audience is core.LinkAudience.Public

    dbx_md.link_permissions.resolved_visibility = sharing.ResolvedVisibility.team_only

    md = convert_shared_link_metadata(dbx_md)

    assert md.link_permissions.effective_audience is core.LinkAudience.Team
    assert md.link_permissions.require_password is False

    dbx_md.link_permissions.resolved_visibility = (
        sharing.ResolvedVisibility.team_and_password
    )

    md = convert_shared_link_metadata(dbx_md)

    assert md.link_permissions.effective_audience is core.LinkAudience.Team
    assert md.link_permissions.require_password is True

    dbx_md.link_permissions.resolved_visibility = sharing.ResolvedVisibility.password

    md = convert_shared_link_metadata(dbx_md)

    assert md.link_permissions.effective_audience is core.LinkAudience.Other
    assert md.link_permissions.require_password is True

    dbx_md.link_permissions.resolved_visibility = sharing.ResolvedVisibility.no_one

    md = convert_shared_link_metadata(dbx_md)

    assert md.link_permissions.effective_audience is core.LinkAudience.NoOne
    assert md.link_permissions.require_password is False
