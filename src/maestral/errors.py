# -*- coding: utf-8 -*-
"""
This module defines Maestral's error classes. It should be kept free of memory heavy
imports.

All errors inherit from :class:`MaestralApiError` which has title and message attributes
to display the error to the user. Errors which are related to syncing a specific
file or folder inherit from :class:`SyncError`, a subclass of :class:`MaestralApiError`.
"""

from typing import Optional


class MaestralApiError(Exception):
    """Base class for Maestral errors

    MaestralApiError provides attributes that can be used to generate human-readable
    error messages and metadata regarding affected file paths (if any).

    Errors originating from the Dropbox API or the 'local API' both inherit from
    MaestralApiError.

    :param title: A short description of the error type. This can be used in a CLI or
        GUI to give a short error summary.
    :param message: A more verbose description which can include instructions on how to
        proceed to fix the error.
    :param dbx_path: Dropbox path of the file that caused the error.
    :param dbx_path_dst: Dropbox destination path of the file that caused the error.
        This should be set for instance when error occurs when moving an item.
    :param local_path: Local path of the file that caused the error.
    :param local_path_dst: Local destination path of the file that caused the error.
        This should be set for instance when error occurs when moving an item.
    """

    def __init__(
        self,
        title: str,
        message: str,
        dbx_path: Optional[str] = None,
        dbx_path_dst: Optional[str] = None,
        local_path: Optional[str] = None,
        local_path_dst: Optional[str] = None,
    ) -> None:
        self.title = title
        self.message = message
        self.dbx_path = dbx_path
        self.dbx_path_dst = dbx_path_dst
        self.local_path = local_path
        self.local_path_dst = local_path_dst

    def __str__(self) -> str:
        return ". ".join([self.title, self.message])


# ==== regular sync errors =============================================================


class SyncError(MaestralApiError):
    """Base class for recoverable sync issues."""

    pass


class InsufficientPermissionsError(SyncError):
    """Raised when accessing a file or folder fails due to insufficient permissions,
    both locally and on Dropbox servers."""

    pass


class InsufficientSpaceError(SyncError):
    """Raised when the Dropbox account or local drive has insufficient storage space."""

    pass


class PathError(SyncError):
    """Raised when there is an issue with the provided file or folder path such as
    invalid characters, a too long file name, etc."""

    pass


class NotFoundError(SyncError):
    """Raised when a file or folder is requested but does not exist."""

    pass


class ConflictError(SyncError):
    """Raised when trying to create a file or folder which already exists."""

    pass


class FileConflictError(ConflictError):
    """Raised when trying to create a file which already exists."""

    pass


class FolderConflictError(SyncError):
    """Raised when trying to create or folder which already exists."""

    pass


class IsAFolderError(SyncError):
    """Raised when a file is required but a folder is provided."""

    pass


class NotAFolderError(SyncError):
    """Raised when a folder is required but a file is provided."""

    pass


class DropboxServerError(SyncError):
    """Raised in case of internal Dropbox errors."""

    pass


class RestrictedContentError(SyncError):
    """Raised when trying to sync restricted content, for instance when adding a file
    with a DMCA takedown notice to a public folder."""

    pass


class UnsupportedFileError(SyncError):
    """Raised when this file type cannot be downloaded but only exported. This is the
    case for G-suite files."""

    pass


class FileSizeError(SyncError):
    """Raised when attempting to upload a file larger than 350 GB in an upload session
    or larger than 150 MB in a single upload. Also raised when attempting to download a
    file with a size that exceeds file system's limit."""

    pass


class FileReadError(SyncError):
    """Raised when reading a local file failed."""

    pass


# ==== errors which are not related to a specific sync event ===========================


class NotLinkedError(MaestralApiError):
    """Raised when no Dropbox account is linked."""

    pass


class InvalidDbidError(MaestralApiError):
    """Raised when the given Dropbox ID does not correspond to an existing account."""

    pass


class KeyringAccessError(MaestralApiError):
    """Raised when retrieving a saved auth token from the user keyring fails."""

    pass


class NoDropboxDirError(MaestralApiError):
    """Raised when the local Dropbox folder cannot be found."""

    pass


class CacheDirError(MaestralApiError):
    """Raised when creating the cache directory fails."""

    pass


class InotifyError(MaestralApiError):
    """Raised when the local Dropbox folder is too large to monitor with inotify."""

    pass


class OutOfMemoryError(MaestralApiError):
    """Raised when there is insufficient memory to complete an operation."""

    pass


class DatabaseError(MaestralApiError):
    """Raised when reading or writing to the database fails."""

    pass


class DropboxAuthError(MaestralApiError):
    """Raised when authentication fails."""

    pass


class TokenExpiredError(DropboxAuthError):
    """Raised when authentication fails because the user's token has expired."""

    pass


class TokenRevokedError(DropboxAuthError):
    """Raised when authentication fails because the user's token has been revoked."""

    pass


class CursorResetError(MaestralApiError):
    """Raised when the cursor used for a longpoll or list-folder request has been
    invalidated. Dropbox will very rarely invalidate a cursor. If this happens, a new
    cursor for the respective folder has to be obtained through files_list_folder. This
    may require re-syncing the entire Dropbox."""

    pass


class BadInputError(MaestralApiError):
    """Raised when an API request is made with bad input. This should not happen
    during syncing but only in case of manual API calls."""

    pass


class BusyError(MaestralApiError):
    """Raised when trying to perform an action which is only possible in the idle
    state and we cannot block or queue the job."""

    pass


class UnsupportedFileTypeForDiff(MaestralApiError):
    """Raised when a diff for an unsupported file type was issued."""

    pass


class SharedLinkError(MaestralApiError):
    """Raised when creating a shared link fails."""

    pass


# connection errors are handled as warnings
# sync errors only appear in the sync errors list
# all other errors raise an error dialog in the GUI

GENERAL_ERRORS = (
    MaestralApiError,
    NotLinkedError,
    InvalidDbidError,
    KeyringAccessError,
    NoDropboxDirError,
    InotifyError,
    RestrictedContentError,
    DatabaseError,
    DropboxAuthError,
    TokenExpiredError,
    TokenRevokedError,
    CursorResetError,
    BadInputError,
    OutOfMemoryError,
    BusyError,
    UnsupportedFileTypeForDiff,
    SharedLinkError,
)

SYNC_ERRORS = (
    SyncError,
    InsufficientPermissionsError,
    InsufficientSpaceError,
    PathError,
    NotFoundError,
    ConflictError,
    IsAFolderError,
    NotAFolderError,
    DropboxServerError,
    RestrictedContentError,
    UnsupportedFileError,
    FileSizeError,
)
