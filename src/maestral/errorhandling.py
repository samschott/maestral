"""
This module contains methods and decorators to convert OSErrors and Dropbox SDK
exceptions to instances of :exc:`maestral.exceptions.MaestralApiError`.
"""

from __future__ import annotations

# system imports
import os
import errno
import contextlib
from typing import Iterator, Union, TypeVar, Callable, Any

# external imports
import requests
from dropbox import files, sharing, users, async_, auth, common
from dropbox import exceptions
from dropbox.stone_validators import ValidationError

# local imports
from .exceptions import (
    MaestralApiError,
    SyncError,
    InsufficientPermissionsError,
    PathError,
    FileReadError,
    InsufficientSpaceError,
    FileConflictError,
    FolderConflictError,
    ConflictError,
    UnsupportedFileError,
    RestrictedContentError,
    NotFoundError,
    NotAFolderError,
    IsAFolderError,
    FileSizeError,
    SymlinkError,
    OutOfMemoryError,
    BadInputError,
    DropboxAuthError,
    TokenExpiredError,
    TokenRevokedError,
    CursorResetError,
    DropboxServerError,
    InvalidDbidError,
    SharedLinkError,
    DropboxConnectionError,
    PathRootError,
    DataCorruptionError,
)
from .utils.path import fs_max_lengths_for_path


__all__ = [
    "CONNECTION_ERRORS",
    "dropbox_to_maestral_error",
    "os_to_maestral_error",
    "convert_api_errors",
]

CONNECTION_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.RetryError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ConnectionError,
    ConnectionError,
)

LocalError = Union[MaestralApiError, OSError]

FT = TypeVar("FT", bound=Callable[..., Any])


# ==== Conversion functions to generate error messages and types =======================


@contextlib.contextmanager
def convert_api_errors(
    dbx_path: str | None = None, local_path: str | None = None
) -> Iterator[None]:
    """
    A context manager that catches and re-raises instances of :exc:`OSError` and
    :exc:`dropbox.exceptions.DropboxException` as
    :exc:`maestral.exceptions.MaestralApiError` or :exc:`ConnectionError`.

    :param dbx_path: Dropbox path associated with the error.
    :param local_path: Local path associated with the error.
    """
    try:
        yield
    except (exceptions.DropboxException, ValidationError) as exc:
        raise dropbox_to_maestral_error(exc, dbx_path, local_path)
    # Catch connection errors first, they may inherit from OSError.
    except CONNECTION_ERRORS:
        raise DropboxConnectionError(
            "Cannot connect to Dropbox",
            "Please check you internet connection and try again later.",
        )
    except OSError as exc:
        if exc.errno == errno.EPROTOTYPE:
            # Can occur on macOS, see https://bugs.python.org/issue33450.
            raise DropboxConnectionError(
                "Cannot connect to Dropbox",
                "Please check you internet connection and try again later.",
            )
        else:
            raise os_to_maestral_error(exc, dbx_path, local_path)


def os_to_maestral_error(
    exc: OSError, dbx_path: str | None = None, local_path: str | None = None
) -> LocalError:
    """
    Converts a :exc:`OSError` to a :exc:`maestral.exceptions.MaestralApiError` and
    tries to add a reasonably informative error title and message.

    :param exc: Original OSError.
    :param dbx_path: Dropbox path associated with the error.
    :param local_path: Local path associated with the error.
    :returns: Converted exception.
    """
    title = "Could not sync file or folder"
    err_cls: type[MaestralApiError]

    if isinstance(exc, PermissionError):
        err_cls = InsufficientPermissionsError  # subclass of SyncError
        text = "Insufficient read or write permissions for this location."
    elif isinstance(exc, FileNotFoundError):
        err_cls = NotFoundError  # subclass of SyncError
        text = "The given path does not exist."
    elif isinstance(exc, FileExistsError):
        err_cls = ConflictError  # subclass of SyncError
        title = "Could not download file"
        text = "There already is an item at the given path."
    elif isinstance(exc, IsADirectoryError):
        err_cls = IsAFolderError  # subclass of SyncError
        title = "Could not create local file"
        text = "The given path refers to a folder."
    elif isinstance(exc, NotADirectoryError):
        err_cls = NotAFolderError  # subclass of SyncError
        title = "Could not create local folder"
        text = "The given path refers to a file."
    elif exc.errno == errno.ENAMETOOLONG:
        err_cls = PathError  # subclass of SyncError
        title = "Could not create local file"

        try:
            max_name, max_path = fs_max_lengths_for_path(local_path or "/")
        except RuntimeError:
            text = "The file name or path is too long."
        else:
            text = (
                "The file name is too long. File names and paths must be shorter "
                f"than {max_name} and {max_path} characters on your file system, "
                f"respectively."
            )
    elif exc.errno == errno.EINVAL:
        err_cls = PathError  # subclass of SyncError
        title = "Could not create local file"
        text = (
            "The file name contains characters which are not allowed on your file "
            "system. This could be for instance a colon or a trailing period."
        )
    elif exc.errno == errno.EFBIG:
        err_cls = FileSizeError  # subclass of SyncError
        title = "Could not download file"
        text = "The file size too large."
    elif exc.errno == errno.ELOOP:
        err_cls = SymlinkError  # subclass of SyncError
        title = "Cannot upload symlink"
        text = "Symlinks are not currently supported by the public Dropbox API."
    elif exc.errno == errno.ENOSPC:
        err_cls = InsufficientSpaceError  # subclass of SyncError
        title = "Could not download file"
        text = "There is not enough space left on the selected drive."
    elif exc.errno == errno.ENOMEM:
        err_cls = OutOfMemoryError  # subclass of MaestralApiError
        text = "Out of memory. Please reduce the number of memory consuming processes."
    elif exc.errno is not None:
        err_cls = FileReadError
        text = f"Could not access file. Errno {exc.errno}: {os.strerror(exc.errno)}."
    else:
        err_cls = MaestralApiError
        text = str(exc)

    local_path = local_path or exc.filename

    maestral_exc = err_cls(title, text, dbx_path=dbx_path, local_path=local_path)
    maestral_exc.__cause__ = exc

    return maestral_exc


def dropbox_to_maestral_error(
    exc: exceptions.DropboxException | ValidationError | requests.HTTPError,
    dbx_path: str | None = None,
    local_path: str | None = None,
) -> MaestralApiError:
    """
    Converts a Dropbox SDK exception to a :exc:`maestral.exceptions.MaestralApiError`
    and tries to add a reasonably informative error title and message.

    :param exc: Dropbox SDK exception..
    :param dbx_path: Dropbox path associated with the error.
    :param local_path: Local path associated with the error.
    :returns: Converted exception.
    """
    title = "An unexpected error occurred"
    text = "Please contact the developer with the traceback information from the logs."
    err_cls = MaestralApiError

    # ---- Dropbox API Errors ----------------------------------------------------------
    if isinstance(exc, exceptions.ApiError):
        error = exc.error

        if isinstance(error, files.RelocationError):
            title = "Could not move file or folder"
            if error.is_cant_copy_shared_folder():
                text = "Shared folders can’t be copied."
                err_cls = SyncError
            elif error.is_cant_move_folder_into_itself():
                text = "You cannot move a folder into itself."
                err_cls = ConflictError
            elif error.is_cant_move_shared_folder():
                text = "You cannot move the shared folder to the given destination."
                err_cls = SyncError
            elif error.is_cant_nest_shared_folder():
                text = (
                    "Your move operation would result in nested shared folders. "
                    "This is not allowed."
                )
                err_cls = SyncError
            elif error.is_cant_transfer_ownership():
                text = (
                    "Your move operation would result in an ownership transfer. "
                    "Maestral does not currently support this. Please carry out "
                    "the move on the Dropbox website instead."
                )
                err_cls = SyncError
            elif error.is_duplicated_or_nested_paths():
                text = (
                    "There are duplicated/nested paths among the target and "
                    "destination folders."
                )
                err_cls = SyncError
            elif error.is_from_lookup():
                lookup_error = error.get_from_lookup()
                text, err_cls = get_lookup_error_msg(lookup_error)
            elif error.is_from_write():
                write_error = error.get_from_write()
                text, err_cls = get_write_error_msg(write_error)
            elif error.is_insufficient_quota():
                text = (
                    "You do not have enough space on Dropbox to move "
                    "or copy the files."
                )
                err_cls = InsufficientSpaceError
            elif error.is_internal_error():
                text = "Something went on Dropbox’s end. Please try again later."
                err_cls = DropboxServerError
            elif error.is_to():
                to_error = error.get_to()
                text, err_cls = get_write_error_msg(to_error)
            elif error.is_too_many_files():
                text = (
                    "There are more than 10,000 files and folders in one "
                    "request. Please try to move fewer items at once."
                )
                err_cls = SyncError
            elif error.is_cant_move_into_vault():
                vault_error = error.get_cant_move_into_vault()
                if vault_error.is_is_shared_folder():
                    text = "You cannot move a shared folder into the Dropbox Vault."
                else:
                    text = "You cannot move this folder into the Dropbox Vault."
                err_cls = SyncError
            # TODO: uncomment when updating to dropbox >= 11.27.0
            # elif error.is_cant_move_into_family():
            #     family_error = error.get_cant_move_into_family()
            #     if family_error.is_is_shared_folder():
            #         text = "You cannot move a shared folder into the Family folder."
            #     else:
            #         text = "You cannot move this folder into the Family folder."
            #     err_cls = SyncError

        elif isinstance(error, (files.CreateFolderError, files.CreateFolderEntryError)):
            title = "Could not create folder"
            if error.is_path():
                write_error = error.get_path()
                text, err_cls = get_write_error_msg(write_error)

        elif isinstance(error, files.DeleteError):
            title = "Could not delete item"
            if error.is_path_lookup():
                lookup_error = error.get_path_lookup()
                text, err_cls = get_lookup_error_msg(lookup_error)
            elif error.is_path_write():
                write_error = error.get_path_write()
                text, err_cls = get_write_error_msg(write_error)
            elif error.is_too_many_files():
                text = (
                    "There are more than 10,000 files and folders in one "
                    "request. Please try to delete fewer items at once."
                )
                err_cls = SyncError
            elif error.is_too_many_write_operations():
                text = (
                    "There are too many write operations happening in your "
                    "Dropbox. Please try again later."
                )
                err_cls = SyncError

        elif isinstance(error, files.UploadError):
            title = "Could not upload file"
            if error.is_path():
                write_error = error.get_path().reason  # Returns UploadWriteFailed.
                text, err_cls = get_write_error_msg(write_error)
            elif error.is_properties_error():
                # Occurs only for programming error in maestral.
                text = "Invalid property group provided."
            elif error.is_payload_too_large():
                text = "Can only upload in chunks of at most 150 MB."
                err_cls = FileSizeError
            elif error.is_content_hash_mismatch():
                text = "Data corruption during upload. Please try again."
                err_cls = DataCorruptionError

        elif isinstance(error, files.UploadSessionStartError):
            title = "Could not upload file"
            if error.is_concurrent_session_close_not_allowed():
                # Occurs only for programming error in maestral.
                text = "Can not start a closed concurrent upload session."
            elif error.is_concurrent_session_data_not_allowed():
                # Occurs only for programming error in maestral.
                text = (
                    "Uploading data not allowed when starting concurrent upload "
                    "session."
                )
            elif error.is_payload_too_large():
                text = "Can only upload in chunks of at most 150 MB."
                err_cls = SyncError
            elif error.is_content_hash_mismatch():
                text = "Data corruption during upload. Please try again."
                err_cls = DataCorruptionError

        elif isinstance(error, files.UploadSessionFinishError):
            title = "Could not upload file"
            if error.is_lookup_failed():
                session_lookup_error = error.get_lookup_failed()
                text, err_cls = get_session_lookup_error_msg(session_lookup_error)
            elif error.is_path():
                write_error = error.get_path()
                text, err_cls = get_write_error_msg(write_error)
            elif error.is_properties_error():
                # Occurs only for programming error in maestral.
                text = "Invalid property group provided."
            elif error.is_too_many_write_operations():
                text = (
                    "There are too many write operations happening in your "
                    "Dropbox. Please retry again later."
                )
                err_cls = SyncError
            elif error.is_too_many_shared_folder_targets():
                text = (
                    "The batch request commits files into too many different shared "
                    "folders. Please limit your batch request to files contained in a "
                    "single shared folder."
                )
                err_cls = SyncError
            elif error.is_payload_too_large():
                text = "Can only upload in chunks of at most 150 MB."
                err_cls = SyncError
            elif error.is_content_hash_mismatch():
                text = "Data corruption during upload. Please try again."
                err_cls = DataCorruptionError

        elif isinstance(error, files.UploadSessionLookupError):
            title = "Could not upload file"
            text, err_cls = get_session_lookup_error_msg(error)

        elif isinstance(error, files.DownloadError):
            title = "Could not download file"
            if error.is_path():
                lookup_error = error.get_path()
                text, err_cls = get_lookup_error_msg(lookup_error)
            elif error.is_unsupported_file():
                text = "This file type cannot be downloaded but must be exported."
                err_cls = UnsupportedFileError

        elif isinstance(error, files.ListFolderError):
            title = "Could not list folder contents"
            if error.is_path():
                lookup_error = error.get_path()
                text, err_cls = get_lookup_error_msg(lookup_error)

        elif isinstance(error, files.ListFolderContinueError):
            title = "Could not list folder contents"
            if error.is_path():
                lookup_error = error.get_path()
                text, err_cls = get_lookup_error_msg(lookup_error)
            elif error.is_reset():
                text = (
                    "Dropbox has reset its sync state. Please rebuild "
                    "Maestral's index to re-sync your Dropbox."
                )
                err_cls = CursorResetError

        elif isinstance(error, files.ListFolderLongpollError):
            title = "Could not get Dropbox changes"
            if error.is_reset():
                text = (
                    "Dropbox has reset its sync state. Please rebuild "
                    "Maestral's index to re-sync your Dropbox."
                )
                err_cls = CursorResetError

        elif isinstance(error, async_.PollError):
            title = "Could not get status of batch job"
            if error.is_internal_error():
                text = (
                    "Something went wrong with the job on Dropbox’s end. Please "
                    "verify on the Dropbox website if the job succeeded and try "
                    "again if it failed."
                )
                err_cls = DropboxServerError
            else:
                # Other tags include invalid_async_job_id.
                # Neither should occur in our SDK usage.
                pass

        elif isinstance(error, files.ListRevisionsError):
            title = "Could not list file revisions"
            if error.is_path():
                lookup_error = error.get_path()
                text, err_cls = get_lookup_error_msg(lookup_error)

        elif isinstance(error, files.RestoreError):
            title = "Could not restore file"
            if error.is_invalid_revision():
                text = "Invalid revision."
                err_cls = NotFoundError
            elif error.is_path_lookup():
                lookup_error = error.get_path_lookup()
                text, err_cls = get_lookup_error_msg(lookup_error)
            elif error.is_path_write():
                write_error = error.get_path_write()
                text, err_cls = get_write_error_msg(write_error)
            elif error.is_in_progress():
                title = "Restore in progress"
                text = "Please check again later if the restore completed"
                err_cls = SyncError

        elif isinstance(error, files.GetMetadataError):
            title = "Could not get metadata"
            if error.is_path():
                lookup_error = error.get_path()
                text, err_cls = get_lookup_error_msg(lookup_error)

        elif isinstance(error, users.GetAccountError):
            title = "Could not get account info"
            if error.is_no_account():
                text = (
                    "An account with the given Dropbox ID does not "
                    "exist or has been deleted"
                )
                err_cls = InvalidDbidError

        elif isinstance(error, sharing.CreateSharedLinkWithSettingsError):
            title = "Could not create shared link"
            if error.is_access_denied():
                text = "You do not have access to create shared links for this path."
                err_cls = InsufficientPermissionsError
            elif error.is_email_not_verified():
                text = "Please verify you email address before creating shared links"
                err_cls = SharedLinkError
            elif error.is_path():
                lookup_error = error.get_path()
                text, err_cls = get_lookup_error_msg(lookup_error)
            elif error.is_settings_error():
                settings_error = error.get_settings_error()
                err_cls = SharedLinkError
                if settings_error.is_invalid_settings():
                    text = "Please check if the settings are valid."
                elif settings_error.is_not_authorized():
                    text = "Basic accounts do not support passwords or expiry dates."
            elif error.is_shared_link_already_exists():
                text = "The shared link already exists."
                err_cls = SharedLinkError

        elif isinstance(error, sharing.RevokeSharedLinkError):
            title = "Could not revoke shared link"
            if error.is_shared_link_malformed():
                text = "The shared link is malformed."
                err_cls = SharedLinkError
            elif error.is_shared_link_not_found():
                text = "The given link does not exist."
                err_cls = NotFoundError
            elif error.is_shared_link_access_denied():
                text = "You do not have access to revoke the shared link."
                err_cls = InsufficientPermissionsError
            elif error.is_unsupported_link_type():
                text = "The link type is not supported."
                err_cls = SharedLinkError

        elif isinstance(error, sharing.ListSharedLinksError):
            title = "Could not list shared links"
            if error.is_path():
                lookup_error = error.get_path()
                text, err_cls = get_lookup_error_msg(lookup_error)
            elif error.is_reset():
                text = "Please try again later."
                err_cls = SharedLinkError

        elif isinstance(error, common.PathRootError):
            if error.is_no_permission():
                text = "You don't have permission to access this namespace."
                err_cls = InsufficientPermissionsError
            elif error.is_invalid_root():
                text = "Invalid root namespace."
                err_cls = SyncError
        elif isinstance(error, sharing.ShareFolderErrorBase):
            title = "Could not share folder"
            if error.is_email_unverified():
                text = (
                    "You need to verify your email address before creating shared "
                    "folders."
                )
                err_cls = SyncError
            elif error.is_bad_path():
                path_error = error.get_bad_path()
                text, err_cls = get_bad_path_error_msg(path_error)
            elif error.is_team_policy_disallows_member_policy():
                text = "Team policy does not allow sharing with the specified members."
                err_cls = InsufficientPermissionsError
            elif error.is_disallowed_shared_link_policy():
                text = "Team policy does not allow creating the specified shared link."
                err_cls = InsufficientPermissionsError

            elif (
                isinstance(error, sharing.ShareFolderError) and error.is_no_permission()
            ):
                text = "You don't have permissions to share this folder."
                err_cls = InsufficientPermissionsError

    # ---- Authentication errors -------------------------------------------------------
    elif isinstance(exc, exceptions.AuthError):
        error = exc.error
        if isinstance(error, auth.AuthError):
            if error.is_expired_access_token():
                err_cls = TokenExpiredError
                title = "Authentication error"
                text = (
                    "Maestral's access to your Dropbox has expired. Please relink "
                    "to continue syncing."
                )
            elif error.is_invalid_access_token():
                err_cls = TokenRevokedError
                title = "Authentication error"
                text = (
                    "Maestral's access to your Dropbox has been revoked. Please "
                    "relink to continue syncing."
                )
            elif error.is_user_suspended():
                err_cls = DropboxAuthError
                title = "Authentication error"
                text = "Your user account has been suspended."
            elif error.is_missing_scope():
                scope_error = error.get_missing_scope()
                required_scope = scope_error.required_scope
                err_cls = InsufficientPermissionsError
                title = "Insufficient permissions"
                text = f"Performing this action requires the {required_scope} scope."
            else:
                # Other tags are invalid_select_admin, invalid_select_user,
                # route_access_denied. Neither should occur in our SDK
                # usage.
                pass

        else:
            err_cls = DropboxAuthError
            title = "Authentication error"
            text = "Please check if you can log in on the Dropbox website."

    # ---- Namespace Errors ------------------------------------------------------------
    elif isinstance(exc, exceptions.PathRootError):
        error = exc.error
        err_cls = PathRootError
        title = "Invalid root namespace"

        if isinstance(error, common.PathRootError):
            if error.is_no_permission():
                text = "You don't have permission to access this namespace."
            elif error.is_invalid_root():
                text = "The given namespace does not exist."
            elif error.is_other():
                text = "An unexpected error occurred with the given namespace."

    # ---- Bad input errors ------------------------------------------------------------
    # Should only occur due to user input from console scripts.
    elif isinstance(exc, (exceptions.BadInputError, ValidationError)):
        err_cls = BadInputError
        title = "Bad input to API call"
        text = exc.message

    # ---- Internal Dropbox error ------------------------------------------------------
    elif isinstance(exc, exceptions.InternalServerError):
        err_cls = DropboxServerError
        title = "Dropbox server error"
        text = (
            "Something went wrong on Dropbox’s end. Please check on status.dropbox.com "
            "if their services are up and running and try again later."
        )
    # ---- Errors which are passed through by the SDK ----------------------------------
    elif isinstance(exc, exceptions.HttpError):
        text = exc.body

    maestral_exc = err_cls(title, text, dbx_path=dbx_path, local_path=local_path)
    maestral_exc.__cause__ = exc

    return maestral_exc


def get_write_error_msg(write_error: files.WriteError) -> tuple[str, type[SyncError]]:
    text = ""
    err_cls = SyncError

    if write_error.is_conflict():
        conflict = write_error.get_conflict()
        if conflict.is_file():
            text = (
                "Could not write to the target path because another file "
                "was in the way."
            )
            err_cls = FileConflictError
        elif conflict.is_folder():
            text = (
                "Could not write to the target path because another folder "
                "was in the way."
            )
            err_cls = FolderConflictError
        elif conflict.is_file_ancestor():
            text = (
                "Could not create parent folders because another file "
                "was in the way."
            )
            err_cls = FileConflictError
        else:
            text = (
                "Could not write to the target path because another file or "
                "folder was in the way."
            )
            err_cls = ConflictError
    elif write_error.is_disallowed_name():
        text = "Dropbox will not save the file or folder because of its name."
        err_cls = PathError
    elif write_error.is_insufficient_space():
        text = "You do not have enough space on Dropbox to move or copy the files."
        err_cls = InsufficientSpaceError
    elif write_error.is_malformed_path():
        text = (
            "The destination path contains incompatible characters. Paths may not end "
            "with a slash or whitespace or contain some characters such as emojis."
        )
        err_cls = PathError
    elif write_error.is_no_write_permission():
        text = "You do not have permissions to write to the target location."
        err_cls = InsufficientPermissionsError
    elif write_error.is_team_folder():
        text = "You cannot move or delete team folders through Maestral."
    elif write_error.is_too_many_write_operations():
        text = (
            "There are too many write operations in your Dropbox. Please "
            "try again later."
        )
    elif write_error.is_operation_suppressed():
        text = "This file operation is not allowed at this path."

    return text, err_cls


def get_lookup_error_msg(
    lookup_error: files.LookupError,
) -> tuple[str, type[SyncError]]:
    err_cls = SyncError

    if lookup_error.is_malformed_path():
        text = "The path is invalid. Paths may not end with a slash or whitespace."
        err_cls = PathError
    elif lookup_error.is_not_file():
        text = "The given path refers to a folder."
        err_cls = IsAFolderError
    elif lookup_error.is_not_folder():
        text = "The given path refers to a file."
        err_cls = NotAFolderError
    elif lookup_error.is_not_found():
        text = "There is nothing at the given path."
        err_cls = NotFoundError
    elif lookup_error.is_restricted_content():
        text = (
            "The file cannot be transferred because the content is restricted. For "
            "example, sometimes there are legal restrictions due to copyright "
            "claims."
        )
        err_cls = RestrictedContentError
    elif lookup_error.is_unsupported_content_type():
        text = "This file type is currently not supported for syncing."
        err_cls = UnsupportedFileError
    elif lookup_error.is_locked():
        text = "The given path is locked."
        err_cls = InsufficientPermissionsError
    else:
        text = "An unexpected error occurred. Please try again later."

    return text, err_cls


def get_session_lookup_error_msg(
    session_lookup_error: files.UploadSessionLookupError,
) -> tuple[str, type[SyncError]]:
    err_cls = SyncError

    if session_lookup_error.is_closed():
        text = "Cannot append data to a closed upload session."
    elif session_lookup_error.is_incorrect_offset():
        text = "A network error occurred during the upload session."
        err_cls = DataCorruptionError
    elif session_lookup_error.is_not_closed():
        text = "Upload session is still open, cannot finish."
    elif session_lookup_error.is_not_found():
        text = (
            "The upload session ID was not found or has expired. "
            "Upload sessions are valid for 48 hours."
        )
    elif session_lookup_error.is_too_large():
        text = "You can only upload files up to 350 GB."
        err_cls = FileSizeError
    elif session_lookup_error.is_payload_too_large():
        text = "Can only upload in chunks of at most 150 MB."
    elif (
        isinstance(session_lookup_error, files.UploadSessionAppendError)
        and session_lookup_error.is_content_hash_mismatch()
    ):
        text = "A network error occurred during the upload session."
        err_cls = DataCorruptionError
    else:
        text = "An unexpected error occurred. Please try again later."

    return text, err_cls


def get_bad_path_error_msg(
    path_error: sharing.SharePathError,
) -> tuple[str, type[SyncError]]:
    err_cls = SyncError

    if path_error.is_is_file():
        text = "A file is at the specified path."
        err_cls = FileConflictError
    elif path_error.is_inside_shared_folder():
        text = "Cannot share a folder inside a shared folder."
    elif path_error.is_contains_shared_folder():
        text = "Cannot share a folder that contains a shared folder."
    elif path_error.is_contains_app_folder():
        text = "Cannot share a folder that contains an app folder."
    elif path_error.is_contains_team_folder():
        text = "Cannot share a folder that contains a team folder."
    elif path_error.is_is_app_folder():
        text = "Cannot share app folders."
    elif path_error.is_inside_app_folder():
        text = "Cannot share a folder inside an app folder."
    elif path_error.is_is_public_folder():
        text = "A public folder can't be shared this way. Use a public link instead."
    elif path_error.is_inside_public_folder():
        text = (
            "A folder inside a public folder can't be shared this way. Use a public "
            "link instead."
        )
    elif path_error.is_already_shared():
        err_cls = FolderConflictError
        text = "The folder is already shared."
    elif path_error.is_invalid_path():
        text = "The path is not valid."
    elif path_error.is_is_osx_package():
        text = "Cannot share macOS packages."
    elif path_error.is_inside_osx_package():
        text = "Cannot share folders inside macOS packages."
    elif path_error.is_is_vault():
        text = "Cannot share the Vault folder."
    elif path_error.is_is_vault_locked():
        text = "Cannot share a folder inside a locked Vault."
    elif path_error.is_is_family():
        text = "Cannot share the Family folder."
    else:
        text = "An unexpected error occurred. Please try again later."

    return text, err_cls
