from dropbox import users, users_common, common, team_common
from maestral.client import (
    convert_account,
    convert_full_account,
    convert_space_usage,
)
from maestral import core


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
