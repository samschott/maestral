"""
Dataclasses for our internal and external APIs.
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
from datetime import datetime


# ==== user ============================================================================


class AccountType(Enum):
    """Enum of account types"""

    Basic = "basic"
    Pro = "pro"
    Business = "business"
    Other = "other"


@dataclass
class Team:
    id: str
    name: str


@dataclass
class RootInfo:
    root_namespace_id: str
    home_namespace_id: str


@dataclass
class UserRootInfo(RootInfo):
    pass


@dataclass
class TeamRootInfo(RootInfo):
    home_path: str


@dataclass
class Account:
    account_id: str
    display_name: str
    email: str
    email_verified: bool
    profile_photo_url: str | None
    disabled: bool


@dataclass
class FullAccount(Account):
    country: str | None
    locale: str
    team: Team | None
    team_member_id: str | None
    account_type: AccountType
    root_info: RootInfo


@dataclass
class TeamSpaceUsage:
    used: int
    allocation: int


@dataclass
class SpaceUsage:
    used: int
    allocated: int
    team_usage: TeamSpaceUsage | None


# ==== files ===========================================================================


class WriteMode(Enum):
    """Enum of write modes when uploading a file"""

    Add = "add"
    Update = "update"
    Overwrite = "overwrite"


@dataclass
class SharingInfo:
    read_only: bool


@dataclass
class Metadata:
    name: str
    path_lower: str
    path_display: str


@dataclass
class DeletedMetadata(Metadata):
    pass


@dataclass
class FileMetadata(Metadata):
    id: str
    client_modified: datetime
    server_modified: datetime
    rev: str
    size: int
    symlink_target: str | None
    shared: bool
    modified_by: str | None
    is_downloadable: bool
    content_hash: str


@dataclass
class FolderMetadata(Metadata):
    id: str
    shared: bool


@dataclass
class ListFolderResult:
    entries: list[Metadata]
    has_more: bool
    cursor: str


# ==== sharing =========================================================================


class LinkAccessLevel(Enum):
    """Enum of access levels to shared links"""

    Viewer = "viewer"
    Editor = "editor"
    Other = "other"


class LinkAudience(Enum):
    """Enum of shared link audience"""

    Public = "public"
    Team = "team"
    NoOne = "no_one"
    Other = "other"


@dataclass
class LinkPermissions:
    can_revoke: bool
    allow_download: bool
    effective_audience: LinkAudience
    link_access_level: LinkAccessLevel
    require_password: bool | None


@dataclass
class SharedLinkMetadata:
    url: str
    name: str
    path_lower: str | None
    expires: datetime | None
    link_permissions: LinkPermissions


@dataclass
class ListSharedLinkResult:
    entries: list[SharedLinkMetadata]
    has_more: bool
    cursor: str


# ==== update checks ===================================================================


@dataclass
class UpdateCheckResult:
    update_available: bool
    latest_release: str
    release_notes: str
