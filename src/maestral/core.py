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
    """A group of users with joint access to shared folders"""

    id: str
    """Unique identifier of the team"""
    name: str
    """Display name of the team"""


@dataclass
class RootInfo:
    """Namespace info for the root of a shared filesystem"""

    root_namespace_id: str
    """Unique ID of the user's root namespace"""
    home_namespace_id: str
    """Unique ID of the user's personal namespace

    This will be different from :attr:`root_namespace_id` when Maestral is set up to
    sync the shared folder of a team.
    """


@dataclass
class UserRootInfo(RootInfo):
    pass


@dataclass
class TeamRootInfo(RootInfo):
    home_path: str
    """Path of the user's personal home folder relative to the root namespace

    Only present for accounts set up as part of a team when syncing the entire team's
    folder.
    """


@dataclass
class Account:
    """Represents the user's account"""

    account_id: str
    """Unique account ID"""
    display_name: str
    """The user's name for display purposes"""
    email: str
    """The user's email address"""
    email_verified: bool
    """Whether the email address was verified"""
    profile_photo_url: str | None
    """A URL to the user's photo"""
    disabled: bool
    """Whether the account is disabled"""


@dataclass
class FullAccount(Account):
    """Represents the user's account and sync information"""

    country: str | None
    """The user's country"""
    locale: str
    """The user's locale"""
    team: Team | None
    """The team that a user belongs to, if any"""
    team_member_id: str | None
    """The member ID of user in a team, if any"""
    account_type: AccountType
    """The account type"""
    root_info: RootInfo
    """The user's root namespace to sync"""


@dataclass
class SpaceUsage:
    """Space usage information"""

    used: int
    """Space used by in bytes"""
    allocated: int
    """Space available in bytes"""


@dataclass
class PersonalSpaceUsage(SpaceUsage):
    """Space usage information for a user"""

    team_usage: SpaceUsage | None
    """Space usage of a user's team, if any"""


# ==== files ===========================================================================


class WriteMode(Enum):
    """Enum of write modes when uploading a file"""

    Add = "add"
    Update = "update"
    Overwrite = "overwrite"


@dataclass
class Metadata:
    """Base class for sync item metadata"""

    name: str
    """Name of the file or folder"""
    path_lower: str
    """Normalised path on the server"""
    path_display: str
    """Cased path for display purposes and the local file system"""


@dataclass
class DeletedMetadata(Metadata):
    """Metadata of a deleted item"""

    pass


@dataclass
class FileMetadata(Metadata):
    """File metadata"""

    id: str
    """Unique ID on the server"""
    client_modified: datetime
    """Modified time in UTC as provided by clients"""
    server_modified: datetime
    """Server-side modified time in UTC"""
    rev: str
    """Unique ID of this version of a file"""
    size: int
    """File size in bytes"""
    symlink_target: str | None
    """If the file is a symlink, path of the target relative to the root namespace"""
    shared: bool
    """Whether the file is shared"""
    modified_by: str | None
    """Unique ID of the account that created / modified this revision"""
    is_downloadable: bool
    """Whether the file can be downloaded"""
    content_hash: str
    """A content hash of the file"""


@dataclass
class FolderMetadata(Metadata):
    """Folder metadata"""

    id: str
    """Unique ID on the server"""
    shared: bool
    """Whether the folder is shared"""


@dataclass
class ListFolderResult:
    """Result from listing the contents of a folder"""

    entries: list[Metadata]
    """List of entries"""
    has_more: bool
    """Whether there are more entries than listed"""
    cursor: str
    """Cursor to iterate and fetch more entries"""


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
    """Permissions for a shared link"""

    can_revoke: bool
    """If the link can be revoked"""
    allow_download: bool
    """If the link allows users to download the item"""
    effective_audience: LinkAudience
    """The effective audience of link (who can use it)"""
    link_access_level: LinkAccessLevel
    """The type of access that the link grants to the item (how they can use it)"""
    require_password: bool | None
    """Whether a password is required when accessing the item through this link

    Note that users who already have access to an item otherwise will not need a
    password regardless of this value."""


@dataclass
class SharedLinkMetadata:
    """Metadata for a shared link"""

    url: str
    """The URL string"""
    name: str
    """The basename of the item"""
    path_lower: str | None
    """The normalised path of the item"""
    expires: datetime | None
    """Expiry time for a link in UTC"""
    link_permissions: LinkPermissions
    """Permissions that a link grants its users"""


@dataclass
class ListSharedLinkResult:
    """Result from listing shared links"""

    entries: list[SharedLinkMetadata]
    """List of shared link metadata"""
    has_more: bool
    """Whether there are more items to fetch"""
    cursor: str
    """A cursor to continue iterating over shared links"""


# ==== update checks ===================================================================


@dataclass
class UpdateCheckResult:
    """Information on update availability"""

    update_available: bool
    """Whether an update to Maestral is available"""
    latest_release: str
    """The latest release that can be updated to"""
    release_notes: str
    """Release notes for all releases between the currently running version up to and
    including the latest version"""
