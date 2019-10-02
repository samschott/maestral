# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# external packages
import dropbox
import requests


CONNECTION_ERROR_MSG = ("Cannot connect to Dropbox servers. Please check " +
                        "your internet connection and try again later.")


class RevFileError(Exception):
    """Raised when the rev file exists but cannot be read."""
    pass


class DropboxDeletedError(Exception):
    """Raised when the local Dropbox folder cannot be found."""
    pass


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
        return "'{0}': {1}. {2}".format(self.dbx_path, self.title, self.message)


class InsufficientPermissionsError(MaestralApiError):
    """Raised when writing a file / folder fails due to insufficient permissions,
    both locally and on Dropbox servers."""
    pass


class InsufficientSpaceError(MaestralApiError):
    """Raised when the Dropbox account has insufficient storage space."""
    pass


class PathError(MaestralApiError):
    """Raised when there is an issue with the provided file / folder path. Refer to the
    ``message``` attribute for details."""
    pass


class ExcludedItemError(MaestralApiError):
    """Raised when an item which is excluded form syncing is created locally."""
    pass


class DropboxServerError(MaestralApiError):
    """Raised in case of internal Dropbox errors."""
    pass


class RestrictedContentError(MaestralApiError):
    """Raised for instance when trying add a file with a DMCA takedown notice to a
    public folder."""
    pass


class DropboxAuthError(MaestralApiError):
    """Raised when authentication fails. Refer to the ``message``` attribute for
    details."""
    pass


class TokenExpiredError(DropboxAuthError):
    """Raised when authentication fails because the user's token has expired."""
    pass


class BadInputError(MaestralApiError):
    """Raised when an API request is made with bad input. This should not happen
    during syncing but only in case of manual API calls."""
    pass


class CursorResetError(MaestralApiError):
    """Raised when the cursor used for a longpoll or list-folder request has been
    invalidated. Dropbox should very rarely invalidate a cursor. If this happens, a new
    cursor for the respective folder has to be obtained through files_list_folder. This
    may require re-syncing the entire Dropbox."""
    pass


class UnsupportedFileError(MaestralApiError):
    """Raised when this file type cannot be downloaded but only exported. This is the
    case for G-suite files, e.g., google sheets on Dropbox cannot be downloaded but
    must be exported as '.xlsx' files."""
    pass


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


# TODO: improve checks for non-downloadable files
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

    err_type = MaestralApiError

    # --------------------------- Dropbox API Errors -------------------------------------
    if isinstance(exc, dropbox.exceptions.ApiError):

        title = "Dropbox Error"
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
                    err_type = PathError
                elif error.is_cant_nest_shared_folder():
                    text = ("Your move operation would result in nested shared folders. "
                            "This is not allowed.")
                    err_type = PathError
                elif error.is_cant_transfer_ownership():
                    text = ("Your move operation would result in an ownership transfer. "
                            "Maestral does not currently support this. Please carry out "
                            "the move on the Dropbox website instead.")
                elif error.is_duplicated_or_nested_paths():
                    text = ("There are duplicated/nested paths among the target and "
                            "destination folders.")
                    err_type = PathError
                elif error.is_from_lookup():
                    lookup_error = error.get_from_lookup()
                    text, err_type = _get_lookup_error_msg(lookup_error)
                elif error.is_insufficient_quota():
                    text = ("You do not have enough space on Dropbox to move "
                            "or copy the files.")
                    err_type = InsufficientSpaceError
                elif error.is_to():
                    to_error = error.get_to()
                    text, err_type = _get_write_error_msg(to_error)
                elif error.is_from_write():
                    write_error = error.get_from_write()
                    text, err_type = _get_write_error_msg(write_error)
                elif error.is_internal_error():
                    text = ("Something went wrong with the job on Dropbox’s end. Please "
                            "verify on the Dropbox website if the move succeeded and try "
                            "again if it failed. This should happen very rarely.")
                    err_type = DropboxServerError

            if isinstance(error, dropbox.files.CreateFolderError):
                title = "Could not create folder"
                if error.is_path():
                    write_error = error.get_path()
                    text, err_type = _get_write_error_msg(write_error)

            if isinstance(error, dropbox.files.DeleteError):
                title = "Could not delete item"
                if error.is_path_lookup():
                    lookup_error = error.get_path_lookup()
                    text, err_type = _get_lookup_error_msg(lookup_error)
                elif error.is_path_write():
                    write_error = error.get_path_write()
                    text, err_type = _get_write_error_msg(write_error)
                elif error.is_too_many_files():
                    text = ("There are too many files in one request. Please "
                            "try to delete fewer files at once.")
                elif error.is_too_many_write_operations():
                    text = ("There are too many write operations your "
                            "Dropbox. Please try again later.")

            if isinstance(error, dropbox.files.UploadError):
                title = "Could not upload file"
                if error.is_path():
                    write_error = error.get_path().reason  # returns UploadWriteFailed
                    text, err_type = _get_write_error_msg(write_error)
                elif error.is_properties_error():
                    pass

            if isinstance(error, dropbox.files.UploadSessionFinishError):
                title = "Could not upload file"
                if error.is_path():
                    write_error = error.get_path()
                    text, err_type = _get_write_error_msg(write_error)
                elif error.is_lookup_failed():
                    pass

            if isinstance(error, dropbox.files.DownloadError):
                title = "Could not download file"
                if error.is_path():
                    lookup_error = error.get_path()
                    text, err_type = _get_lookup_error_msg(lookup_error)

            if isinstance(error, dropbox.files.ListFolderError):
                title = "Could not list folder contents"
                if error.is_path():
                    lookup_error = error.get_path()
                    text, err_type = _get_lookup_error_msg(lookup_error)

            if isinstance(exc.error, dropbox.files.ListFolderContinueError):
                title = "Could not list folder contents"
                if error.is_path():
                    lookup_error = error.get_path()
                    text, err_type = _get_lookup_error_msg(lookup_error)
                elif error.is_reset():
                    text = "Cursor has been reset by Dropbox. Please try again."
                    err_type = CursorResetError

            if isinstance(exc.error, dropbox.files.ListFolderLongpollError):
                title = "Could not get Dropbox changes"
                if error.is_reset():
                    text = "Cursor has been reset by Dropbox. Please try again."
                    err_type = CursorResetError

        if text is None:
            text = ("An unexpected error occurred. Please contact the Maestral "
                    "developer with the traceback information.")

    # ----------------------- Local read / write errors ----------------------------------
    elif isinstance(exc, PermissionError):
        title = "Could not download file"
        text = "Insufficient read or write permissions for the download location."
        err_type = InsufficientPermissionsError
    elif isinstance(exc, FileNotFoundError):
        title = "Could not download file"
        text = "The given download path is invalid."
        err_type = PathError
    elif isinstance(exc, IsADirectoryError):
        title = "Could not download file"
        text = "The given download path is a directory."
        err_type = PathError

    # ----------------------- Authentication errors --------------------------------------
    elif isinstance(exc, dropbox.exceptions.AuthError):
        error = exc.error
        if isinstance(error, dropbox.auth.AuthError) and error.is_expired_access_token():
            title = "Expired Dropbox access"
            text = ("Maestral's access to your Dropbox has expired. Please relink "
                    "to continue syncing.")
            err_type = TokenExpiredError
        else:
            title = "Authentication error"
            text = ("Maestral's access to your Dropbox has been revoked. Please "
                    "relink to continue syncing.")
            err_type = DropboxAuthError

    # -------------------------- OAuth2 flow errors --------------------------------------
    elif isinstance(exc, requests.HTTPError):
        title = "Authentication failed"
        text = "Please make sure that you entered the correct authentication code."
        err_type = DropboxAuthError
    elif isinstance(exc, dropbox.oauth.BadStateException):
        title = "Authentication session expired."
        text = "The authentication session expired. Please try again."
        err_type = DropboxAuthError
    elif isinstance(exc, dropbox.oauth.NotApprovedException):
        title = "Not approved error"
        text = "Please grant Maestral access to your Dropbox to start syncing."
        err_type = DropboxAuthError

    # ----------------------------- Bad input errors -------------------------------------
    # should only occur due to user input from console scripts
    elif isinstance(exc, dropbox.exceptions.BadInputError):
        if ("The given OAuth 2 access token is malformed" in exc.message or
                "Invalid authorization value in HTTP header" in exc.message):
            title = "Authentication failed"
            text = "Please make sure that you entered the correct authentication code."
            err_type = DropboxAuthError
        else:
            title = "Dropbox error"
            text = exc.message
            err_type = BadInputError

    # -------------------------- Everything else -----------------------------------------
    else:
        title = exc.args[0]
        text = None

    return err_type(title, text, dbx_path=dbx_path, local_path=local_path)


def _get_write_error_msg(write_error):
    assert isinstance(write_error, dropbox.files.WriteError)

    text = None
    err_type = MaestralApiError

    if write_error.is_conflict():
        text = ("Could not write to the target path because another file or "
                "folder was in the way.")
        err_type = PathError
    elif write_error.is_disallowed_name():
        text = "Dropbox will not save the file or folder because of its name."
        err_type = PathError
    elif write_error.is_insufficient_space():
        text = "You do not have enough space on Dropbox to move or copy the files."
        err_type = InsufficientSpaceError
    elif write_error.is_malformed_path():
        text = ("The destination path is invalid. Paths may not end with a slash or "
                "whitespace.")
        err_type = PathError
    elif write_error.is_no_write_permission():
        text = "You do not have permissions to write to the target location."
        err_type = InsufficientPermissionsError
    elif write_error.is_team_folder():
        text = "You cannot move or delete team folders through Maestral."
    elif write_error.is_too_many_write_operations():
        text = ("There are too many write operations in your Dropbox. Please "
                "try again later.")

    return text, err_type


def _get_lookup_error_msg(lookup_error):
    assert isinstance(lookup_error, dropbox.files.LookupError)

    text = None
    err_type = MaestralApiError

    if lookup_error.is_malformed_path():
        text = ("The destination path is invalid. Paths may not end with a slash or "
                "whitespace.")
        err_type = PathError
    elif lookup_error.is_not_file():
        text = "We were expecting a file, but the given path refers to a folder."
        err_type = PathError
    elif lookup_error.is_not_folder():
        text = "We were expecting a folder, but the given path refers to a file."
        err_type = PathError
    elif lookup_error.is_not_found():
        text = "There is nothing at the given path."
        err_type = PathError
    elif lookup_error.is_restricted_content():
        text = ("The file cannot be transferred because the content is restricted. For "
                "example, sometimes there are legal restrictions due to copyright "
                "claims.")
        err_type = RestrictedContentError
    elif lookup_error.is_unsupported_content_type():
        text = "This file type is currently not supported for syncing."
        err_type = UnsupportedFileError

    return text, err_type


# connection errors are handled as warnings
# sync errors only appear in the sync errors list
# all other errors raise an error dialog in the GUI

CONNECTION_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.HTTPError,
    requests.exceptions.ReadTimeout,
    requests.exceptions.RetryError,
    ConnectionError,
)

SYNC_ERRORS = (
    InsufficientPermissionsError,
    InsufficientSpaceError,
    PathError,
    RestrictedContentError,
    UnsupportedFileError,
    ExcludedItemError,
)

OS_FILE_ERRORS = (
    FileExistsError,
    FileNotFoundError,
    InterruptedError,
    IsADirectoryError,
    NotADirectoryError,
    PermissionError,
)
