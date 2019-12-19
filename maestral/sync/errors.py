# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# KEEP FREE OF DROPBOX IMPORTS TO REDUCE MEMORY FOOTPRINT


CONNECTION_ERROR_MSG = ("Cannot connect to Dropbox servers. Please check " +
                        "your internet connection and try again later.")


class MaestralApiError(Exception):
    """
    Base class for errors originating from the Dropbox API or the 'local API'.

    :ivar str title: A short description of the error type.
    :ivar str message: A more verbose description which can include instructions on how to
        proceed to handle the error.
    :ivar str dbx_path: Dropbox path of the file that caused the error.
    :ivar str dbx_path_dst: Dropbox destination path of the file that caused the error.
        This should be set for instance when error occurs when moving a file / folder.
    :ivar str local_path: Local path of the file that caused the error.
    :ivar str local_path_dst: Local destination path of the file that caused the error.
        This should be set for instance when error occurs when moving a file / folder.
    """

    def __init__(self, title, message, dbx_path=None, dbx_path_dst=None,
                 local_path=None, local_path_dst=None):
        self.title = title
        self.message = message
        self.dbx_path = dbx_path
        self.dbx_path_dst = dbx_path_dst
        self.local_path = local_path
        self.local_path_dst = local_path_dst

    def __str__(self):
        if self.dbx_path:
            return f"'{self.dbx_path}': {self.title}. {self.message}"
        else:
            return f"{self.title}: {self.message}"

# regular sync errors

class SyncError(MaestralApiError):
    """Raised for recoverable sync errors which show up as "Sync Issues"."""
    pass


class InsufficientPermissionsError(SyncError):
    """Raised when writing a file / folder fails due to insufficient permissions,
    both locally and on Dropbox servers."""
    pass


class InsufficientSpaceError(SyncError):
    """Raised when the Dropbox account has insufficient storage space."""
    pass


class PathError(SyncError):
    """Raised when there is an issue with the provided file / folder path. Refer to the
    ``message``` attribute for details."""
    pass


class ExcludedItemError(SyncError):
    """Raised when an item which is excluded form syncing is created locally."""
    pass


class DropboxServerError(SyncError):
    """Raised in case of internal Dropbox errors."""
    pass


class RestrictedContentError(SyncError):
    """Raised for instance when trying add a file with a DMCA takedown notice to a
    public folder."""
    pass


class UnsupportedFileError(SyncError):
    """Raised when this file type cannot be downloaded but only exported. This is the
    case for G-suite files, e.g., google sheets on Dropbox cannot be downloaded but
    must be exported as '.xlsx' files."""
    pass


class FileSizeError(SyncError):
    """Raised attempting to upload a file larger than 350 GB in an upload session, or
    larger than 150 MB in a single upload."""
    pass


# fatal errors, require user action


class InotifyError(MaestralApiError):
    """Raised when the local Dropbox folder is too large to monitor with inotify."""
    pass


class DropboxDeletedError(MaestralApiError):
    """Raised when the local Dropbox folder cannot be found."""
    pass


class RevFileError(MaestralApiError):
    """Raised when the rev file exists but cannot be read."""
    pass


class DropboxAuthError(MaestralApiError):
    """Raised when authentication fails. Refer to the ``message``` attribute for
    details."""
    pass


class TokenExpiredError(DropboxAuthError):
    """Raised when authentication fails because the user's token has expired."""
    pass


class CursorResetError(MaestralApiError):
    """Raised when the cursor used for a longpoll or list-folder request has been
    invalidated. Dropbox should very rarely invalidate a cursor. If this happens, a new
    cursor for the respective folder has to be obtained through files_list_folder. This
    may require re-syncing the entire Dropbox."""
    pass


class BadInputError(MaestralApiError):
    """Raised when an API request is made with bad input. This should not happen
    during syncing but only in case of manual API calls."""
    pass


# conversion functions to generate error messages and types

def os_to_maestral_error(exc, dbx_path=None, local_path=None):
    """
    Gets the OSError and tries to add a reasonably informative error message.

    :param exc: Python Exception.
    :param str dbx_path: Dropbox path of file which triggered the error.
    :param str local_path: Local path of file which triggered the error.
    :returns: :class:`MaestralApiError` instance.
    :rtype: :class:`MaestralApiError`
    """
    title = "Could not access or create local item"
    err_type = MaestralApiError
    if isinstance(exc, PermissionError):
        text = "Insufficient read or write permissions for this location."
        err_type = InsufficientPermissionsError
    elif isinstance(exc, FileNotFoundError):
        text = "The given path does not exist."
        err_type = PathError
    else:
        text = None

    return err_type(title, text, dbx_path=dbx_path, local_path=local_path)


def api_to_maestral_error(exc, dbx_path=None, local_path=None):
    """
    Gets the Dropbox API Error and tries to add a reasonably informative error
    message from the mess which is the Python Dropbox SDK exception handling.

    :param exc: :class:`dropbox.exceptions.ApiError` instance.
    :param str dbx_path: Dropbox path of file which triggered the error.
    :param str local_path: Local path of file which triggered the error.
    :returns: :class:`MaestralApiError` instance.
    :rtype: :class:`MaestralApiError`
    """
    import requests
    import dropbox  # import here to reduce memory usage if not needed

    # --------------------------- Dropbox API Errors -------------------------------------
    if isinstance(exc, dropbox.exceptions.ApiError):

        # may be replaced later
        err_cls = SyncError
        title = "Sync Error"
        text = None

        if hasattr(exc, "user_message_text") and exc.user_message_text is not None:
            # if the error contains a user message, pass it on (this rarely happens)
            text = exc.user_message_text
        else:
            # otherwise, analyze the error ourselves and select title and message
            error = exc.error
            if isinstance(error, dropbox.files.RelocationError):
                title = "Could not move folder"
                if error.is_cant_copy_shared_folder():
                    text = "Shared folders can’t be copied."
                elif error.is_cant_move_folder_into_itself():
                    text = "You cannot move a folder into itself."
                    err_cls = PathError
                elif error.is_cant_move_shared_folder():
                    text = "You cannot move the shared folder to the given destination."
                    err_cls = PathError
                elif error.is_cant_nest_shared_folder():
                    text = ("Your move operation would result in nested shared folders. "
                            "This is not allowed.")
                    err_cls = PathError
                elif error.is_cant_transfer_ownership():
                    text = ("Your move operation would result in an ownership transfer. "
                            "Maestral does not currently support this. Please carry out "
                            "the move on the Dropbox website instead.")
                elif error.is_duplicated_or_nested_paths():
                    text = ("There are duplicated/nested paths among the target and "
                            "destination folders.")
                    err_cls = PathError
                elif error.is_from_lookup():
                    lookup_error = error.get_from_lookup()
                    text, err_cls = _get_lookup_error_msg(lookup_error)
                elif error.is_from_write():
                    write_error = error.get_from_write()
                    text, err_cls = _get_write_error_msg(write_error)
                elif error.is_insufficient_quota():
                    text = ("You do not have enough space on Dropbox to move "
                            "or copy the files.")
                    err_cls = InsufficientSpaceError
                elif error.is_internal_error():
                    text = ("Something went wrong with the job on Dropbox’s end. Please "
                            "verify on the Dropbox website if the move succeeded and try "
                            "again if it failed.")
                    err_cls = DropboxServerError
                elif error.is_to():
                    to_error = error.get_to()
                    text, err_cls = _get_write_error_msg(to_error)
                elif error.is_too_many_files():
                    text = ("There are more than 10,000 files and folders in one "
                            "request. Please try to move fewer items at once.")

            if isinstance(error, dropbox.files.CreateFolderError):
                title = "Could not create folder"
                if error.is_path():
                    write_error = error.get_path()
                    text, err_cls = _get_write_error_msg(write_error)

            if isinstance(error, dropbox.files.DeleteError):
                title = "Could not delete item"
                if error.is_path_lookup():
                    lookup_error = error.get_path_lookup()
                    text, err_cls = _get_lookup_error_msg(lookup_error)
                elif error.is_path_write():
                    write_error = error.get_path_write()
                    text, err_cls = _get_write_error_msg(write_error)
                elif error.is_too_many_files():
                    text = ("There are more than 10,000 files and folders in one "
                            "request. Please try to delete fewer items at once.")
                elif error.is_too_many_write_operations():
                    text = ("There are too many write operations happening in your "
                            "Dropbox. Please retry deleting this file later.")

            if isinstance(error, dropbox.files.UploadError):
                title = "Could not upload file"
                if error.is_path():
                    write_error = error.get_path().reason  # returns UploadWriteFailed
                    text, err_cls = _get_write_error_msg(write_error)
                elif error.is_properties_error():
                    pass  # we currently do not use property groups

            if isinstance(error, dropbox.files.UploadSessionFinishError):
                title = "Could not upload file"
                if error.is_lookup_failed():
                    session_lookup_error = error.get_lookup_failed()
                    text, err_cls = _get_session_lookup_error_msg(session_lookup_error)
                elif error.is_path():
                    write_error = error.get_path()
                    text, err_cls = _get_write_error_msg(write_error)
                elif error.is_properties_error():
                    pass  # we currently do not use property groups
                elif error.is_too_many_write_operations():
                    text = ("There are too many write operations happening in your "
                            "Dropbox. Please retry uploading this file later.")

            if isinstance(error, dropbox.files.DownloadError):
                title = "Could not download file"
                if error.is_path():
                    lookup_error = error.get_path()
                    text, err_cls = _get_lookup_error_msg(lookup_error)
                elif error.is_unsupported_file():
                    text = "This file type cannot be downloaded but must be exported."
                    err_cls = UnsupportedFileError

            if isinstance(error, dropbox.files.ListFolderError):
                title = "Could not list folder contents"
                if error.is_path():
                    lookup_error = error.get_path()
                    text, err_cls = _get_lookup_error_msg(lookup_error)

            if isinstance(exc.error, dropbox.files.ListFolderContinueError):
                title = "Could not list folder contents"
                if error.is_path():
                    lookup_error = error.get_path()
                    text, err_cls = _get_lookup_error_msg(lookup_error)
                elif error.is_reset():
                    text = ("Dropbox has reset its sync state. Please rebuild Maestral's "
                            "index to re-sync your Dropbox.")
                    err_cls = CursorResetError

            if isinstance(exc.error, dropbox.files.ListFolderLongpollError):
                title = "Could not get Dropbox changes"
                if error.is_reset():
                    text = ("Dropbox has reset its sync state. Please rebuild Maestral's "
                            "index to re-sync your Dropbox.")
                    err_cls = CursorResetError

        if text is None:
            text = ("An unexpected sync error occurred. Please contact the Maestral "
                    "developer with the traceback information from the logs.")

    # ----------------------- Local read / write errors ----------------------------------
    elif isinstance(exc, PermissionError):
        err_cls = InsufficientPermissionsError
        title = "Could not download file"
        text = "Insufficient read or write permissions for the download location."
    elif isinstance(exc, FileNotFoundError):
        err_cls = PathError
        title = "Could not download file"
        text = "The given download path is invalid."
    elif isinstance(exc, IsADirectoryError):
        err_cls = PathError
        title = "Could not download file"
        text = "The given download path is a directory."

    # ----------------------- Authentication errors --------------------------------------
    elif isinstance(exc, dropbox.exceptions.AuthError):
        error = exc.error
        if isinstance(error, dropbox.auth.AuthError) and error.is_expired_access_token():
            err_cls = TokenExpiredError
            title = "Expired Dropbox access"
            text = ("Maestral's access to your Dropbox has expired. Please relink "
                    "to continue syncing.")
        else:
            err_cls = DropboxAuthError
            title = "Authentication error"
            text = ("Maestral's access to your Dropbox has been revoked. Please "
                    "relink to continue syncing.")

    # -------------------------- OAuth2 flow errors --------------------------------------
    elif isinstance(exc, requests.HTTPError):
        err_cls = DropboxAuthError
        title = "Authentication failed"
        text = "Please make sure that you entered the correct authentication code."
    elif isinstance(exc, dropbox.oauth.BadStateException):
        err_cls = DropboxAuthError
        title = "Authentication session expired."
        text = "The authentication session expired. Please try again."
    elif isinstance(exc, dropbox.oauth.NotApprovedException):
        err_cls = DropboxAuthError
        title = "Not approved error"
        text = "Please grant Maestral access to your Dropbox to start syncing."

    # ----------------------------- Bad input errors -------------------------------------
    # should only occur due to user input from console scripts
    elif isinstance(exc, dropbox.exceptions.BadInputError):
        if ("The given OAuth 2 access token is malformed" in exc.message or
                "Invalid authorization value in HTTP header" in exc.message):
            err_cls = DropboxAuthError
            title = "Authentication failed"
            text = "Please make sure that you entered the correct authentication code."
        else:
            err_cls = BadInputError
            title = "Sync error"
            text = exc.message

    # -------------------------- Everything else -----------------------------------------
    else:
        err_cls = MaestralApiError
        title = exc.args[0]
        text = None

    return err_cls(title, text, dbx_path=dbx_path, local_path=local_path)


def _get_write_error_msg(write_error):

    text = None
    err_cls = SyncError

    if write_error.is_conflict():
        text = ("Could not write to the target path because another file or "
                "folder was in the way.")
        err_cls = PathError
    elif write_error.is_disallowed_name():
        text = "Dropbox will not save the file or folder because of its name."
        err_cls = PathError
    elif write_error.is_insufficient_space():
        text = "You do not have enough space on Dropbox to move or copy the files."
        err_cls = InsufficientSpaceError
    elif write_error.is_malformed_path():
        text = ("The destination path is invalid. Paths may not end with a slash or "
                "whitespace.")
        err_cls = PathError
    elif write_error.is_no_write_permission():
        text = "You do not have permissions to write to the target location."
        err_cls = InsufficientPermissionsError
    elif write_error.is_team_folder():
        text = "You cannot move or delete team folders through Maestral."
    elif write_error.is_too_many_write_operations():
        text = ("There are too many write operations in your Dropbox. Please "
                "try again later.")

    return text, err_cls


def _get_lookup_error_msg(lookup_error):

    text = None
    err_cls = SyncError

    if lookup_error.is_malformed_path():
        text = ("The destination path is invalid. Paths may not end with a slash or "
                "whitespace.")
        err_cls = PathError
    elif lookup_error.is_not_file():
        text = "We were expecting a file, but the given path refers to a folder."
        err_cls = PathError
    elif lookup_error.is_not_folder():
        text = "We were expecting a folder, but the given path refers to a file."
        err_cls = PathError
    elif lookup_error.is_not_found():
        text = "There is nothing at the given path."
        err_cls = PathError
    elif lookup_error.is_restricted_content():
        text = ("The file cannot be transferred because the content is restricted. For "
                "example, sometimes there are legal restrictions due to copyright "
                "claims.")
        err_cls = RestrictedContentError
    elif lookup_error.is_unsupported_content_type():
        text = "This file type is currently not supported for syncing."
        err_cls = UnsupportedFileError

    return text, err_cls


def _get_session_lookup_error_msg(session_lookup_error):

    text = None
    err_cls = SyncError

    if session_lookup_error.is_closed():
        # happens when trying to append data to a closed session
        # this is caused by internal Maestral errors
        pass
    elif session_lookup_error.is_incorrect_offset():
        text = "A network error occurred during the upload session."
    elif session_lookup_error.is_not_closed():
        # happens when trying to finish an open session
        # this is caused by internal Maestral errors
        pass
    elif session_lookup_error.is_not_found():
        text = ("The upload session ID was not found or has expired. "
                "Upload sessions are valid for 48 hours.")
    elif session_lookup_error.is_too_large():
        text = "You can only upload files up to 350 GB."
        err_cls = FileSizeError

    return text, err_cls

# connection errors are handled as warnings
# sync errors only appear in the sync errors list
# all other errors raise an error dialog in the GUI

SYNC_ERRORS = (
    SyncError,
    InsufficientPermissionsError,
    InsufficientSpaceError,
    PathError,
    ExcludedItemError,
    DropboxServerError,
    RestrictedContentError,
    UnsupportedFileError,
    FileSizeError,
)

FATAL_ERRORS = (
    InotifyError,
    DropboxDeletedError,
    RestrictedContentError,
    RevFileError,
    DropboxAuthError,
    TokenExpiredError,
    CursorResetError,
    BadInputError,
)

