# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""
import os
import os.path as osp
import time
import datetime
import logging
import requests

import keyring
from keyring.errors import KeyringLocked
import dropbox
from dropbox import DropboxOAuth2FlowNoRedirect

from maestral.config.main import CONF, SUBFOLDER
from maestral.config.base import get_conf_path
from maestral.utils import is_macos_bundle

if is_macos_bundle:
    # running in a bundle in macOS
    import keyring.backends.OS_X
    keyring.set_keyring(keyring.backends.OS_X.Keyring())

logger = logging.getLogger(__name__)

# create single requests session for all clients
SESSION = dropbox.dropbox.create_session()
USER_AGENT = "Maestral/v0.2"

APP_KEY = os.environ["DROPBOX_API_KEY"]
APP_SECRET = os.environ["DROPBOX_API_SECRET"]


def tobytes(value, unit, bsize=1024):
    """
    Convert size from megabytes to bytes.

    :param int value: Value in bytes.
    :param str unit: Unit to convert to. 'KB' to 'EB' are supported.
    :param int bsize: Conversion between bytes and next higher unit.
    :returns: Converted value in units of `to`.
    :rtype: float
    """
    a = {"KB": 1, "MB": 2, "GB": 3, "TB": 4, "PB": 5, "EB": 6}

    return float(value) * bsize**a[unit.upper()]


def bytesto(value, unit, bsize=1024):
    """
    Convert size from megabytes to bytes.

    :param int value: Value in bytes.
    :param str unit: Unit to convert to. 'KB' to 'EB' are supported.
    :param int bsize: Conversion between bytes and next higher unit.
    :returns: Converted value in units of `to`.
    :rtype: float
    """
    a = {"KB": 1, "MB": 2, "GB": 3, "TB": 4, "PB": 5, "EB": 6}

    return float(value) / bsize**a[unit.upper()]


class SpaceUsage(dropbox.users.SpaceUsage):

    def __str__(self):

        if self.allocation.is_individual():
            used = self.used
            allocated = self.allocation.get_individual().allocated
        elif self.allocation.is_team():
            used = self.allocation.get_team().used
            allocated = self.allocation.get_team().allocated
        else:
            used_gb = bytesto(self.used, "GB")
            return "{:,}GB used".format(used_gb)

        percent = used / allocated * 100
        alloc_gb = bytesto(allocated, "GB")
        str_rep = "{:.1f}% of {:,}GB used".format(percent, alloc_gb)
        return str_rep


class OAuth2Session(object):
    """
    OAuth2Session provides OAuth2 login and token store.

    :ivar app_key: String containing app key provided by Dropbox.
    :ivar app_secret: String containing app secret provided by Dropbox.
    """

    TOKEN_FILE = osp.join(get_conf_path(SUBFOLDER), "o2_store.txt")
    oAuth2FlowResult = None
    access_token = ""
    account_id = CONF.get("account", "account_id")

    def __init__(self, app_key=APP_KEY, app_secret=APP_SECRET):
        self.app_key = app_key
        self.app_secret = app_secret

        self.migrate_to_keyring()

        # load creds
        self.load_creds()

    def migrate_to_keyring(self):

        if os.path.isfile(self.TOKEN_FILE):
            print(" > Migrating access token to keyring...")

            try:
                # load old token
                with open(self.TOKEN_FILE) as f:
                    stored_creds = f.read()
                self.access_token, self.account_id, _ = stored_creds.split("|")

                # migrate old token to keyring
                self.write_creds()
                os.unlink(self.TOKEN_FILE)
                print(" [DONE]")

            except IOError:
                print(" x Could not load old token. Beginning new session.")

        elif keyring.get_password("Maestral", "MaestralUser") and self.account_id:
            print(" > Migrating access token to account_id...")
            self.access_token = keyring.get_password("Maestral", "MaestralUser")
            try:
                keyring.set_password("Maestral", self.account_id, self.access_token)
                keyring.delete_password("Maestral", "MaestralUser")
                print(" [DONE]")
            except KeyringLocked:
                raise KeyringLocked(
                    "Could not access the user keyring to load your authentication "
                    "token. Please make sure that the keyring is unlocked.")

    def link(self):
        auth_flow = DropboxOAuth2FlowNoRedirect(self.app_key, self.app_secret)
        authorize_url = auth_flow.start()
        print("1. Go to: " + authorize_url)
        print("2. Click \"Allow\" (you might have to log in first).")
        print("3. Copy the authorization code.")
        auth_code = input("Enter the authorization code here: ").strip()

        try:
            self.oAuth2FlowResult = auth_flow.finish(auth_code)
            self.access_token = self.oAuth2FlowResult.access_token
            self.account_id = self.oAuth2FlowResult.account_id
        except Exception as exc:
            raise _to_maestral_error(exc) from exc

        self.write_creds()

    def load_creds(self):
        print(" > Loading access token...")
        try:
            t1 = keyring.get_password("Maestral", self.account_id)
            t2 = keyring.get_password("Maestral", "MaestralUser")
            self.access_token = t1 or t2
        except KeyringLocked:
            raise KeyringLocked(
                "Could not access the user keyring to load your authentication token. "
                "Please make sure that the keyring is unlocked.")

        if not self.access_token:
            print(" [FAILED]")
            print(" x Access token not found. Beginning new session.")
            self.link()

    def write_creds(self):
        CONF.set("account", "account_id", self.account_id)
        try:
            keyring.set_password("Maestral", self.account_id, self.access_token)
            print(" > Credentials written.")
        except KeyringLocked:
            logger.error("Could not access the user keyring to save your authentication "
                         "token. Please make sure that the keyring is unlocked.")

    def delete_creds(self):
        CONF.set("account", "account_id", "")
        try:
            keyring.delete_password("Maestral", self.account_id)
            print(" > Credentials removed.")
        except KeyringLocked:
            logger.error("Could not access the user keyring to delete your authentication"
                         " token. Please make sure that the keyring is unlocked.")


# noinspection PyDeprecation
class MaestralApiClient(object):
    """Client for Dropbox SDK.

    This client defines basic methods to wrap Dropbox Python SDK calls, such as creating,
    moving, modifying and deleting files and folders on Dropbox and downloading files from
    Dropbox. MaestralClient also provides methods to wait for and list changes from the
    remote Dropbox.

    All Dropbox API errors are caught and handled here. ConnectionErrors will
    be caught and handled by :class:`MaestralMonitor` instead.

    """

    SDK_VERSION = "2.0"

    def __init__(self):

        # get Dropbox session
        self.auth = OAuth2Session()
        self._last_longpoll = None
        self._backoff = 0
        self._retry_count = 0

        # initialize API client
        self.dbx = dropbox.Dropbox(self.auth.access_token, session=SESSION,
                                   user_agent=USER_AGENT)
        print(" > MaestralClient is ready.")

    def get_account_info(self):
        """
        Gets current account information.

        :returns: :class:`dropbox.users.FullAccount` instance or `None` if failed.
        :rtype: dropbox.users.FullAccount
        """
        res = self.dbx.users_get_current_account()  # this does not raise any API errors

        if res.account_type.is_basic():
            account_type = 'basic'
        elif res.account_type.is_business():
            account_type = 'business'
        elif res.account_type.is_pro():
            account_type = 'pro'
        else:
            account_type = ''

        CONF.set("account", "account_id", res.account_id)
        CONF.set("account", "email", res.email)
        CONF.set("account", "type", account_type)

        return res

    def get_space_usage(self):
        """
        Gets current account space usage.

        :returns: :class:`SpaceUsage` instance or `False` if failed.
        :rtype: SpaceUsage
        """
        res = self.dbx.users_get_space_usage()  # this does not raise any API errors

        # convert from dropbox.users.SpaceUsage to SpaceUsage with nice string
        # representation
        res.__class__ = SpaceUsage

        if res.allocation.is_team():
            CONF.set("account", "usage_type", "team")
        elif res.allocation.is_individual():
            CONF.set("account", "usage_type", "individual")

        CONF.set("account", "usage", str(res))

        return res

    def unlink(self):
        """
        Unlinks the Dropbox account and deletes local sync information.
        """
        self.auth.delete_creds()
        self.dbx.auth_token_revoke()  # this does not raise any API errors

    def get_metadata(self, dbx_path, **kwargs):
        """
        Get metadata for Dropbox entry (file or folder). Returns `None` if no
        metadata is available. Keyword arguments are passed on to Dropbox SDK
        files_get_metadata call.

        :param str dbx_path: Path of folder on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_download_to_file.
        :returns: FileMetadata|FolderMetadata entries or `False` if failed.
        """

        try:
            md = self.dbx.files_get_metadata(dbx_path, **kwargs)
            logger.debug("Retrieved metadata for '{0}'".format(md.path_display))
        except dropbox.exceptions.ApiError as exc:
            # DropboxAPI error is only raised when the item does not exist on Dropbox
            # this is handled on a DEBUG level since we use call `get_metadata` to check
            # if a file exists
            logger.debug("Could not get metadata for '%s': %s", dbx_path, exc)
            md = False

        return md

    def download(self, dbx_path, dst_path, **kwargs):
        """
        Downloads file from Dropbox to our local folder.

        :param str dbx_path: Path to file on Dropbox.
        :param str dst_path: Path to download destination.
        :param kwargs: Keyword arguments for Dropbox SDK files_download_to_file.
        :returns: :class:`FileMetadata` or
            :class:`FolderMetadata` of downloaded item, `False`
            if request fails or `None` if local copy is already in sync.
        """
        # generate local path from dropbox_path and given path parameter
        dst_path_directory = osp.dirname(dst_path)

        if not osp.exists(dst_path_directory):
            try:
                os.mkdir(dst_path_directory)
            except FileExistsError:
                pass

        try:
            md = self.dbx.files_download_to_file(dst_path, dbx_path, **kwargs)
        except dropbox.exceptions.ApiError as exc:
            raise _to_maestral_error(exc, dbx_path) from exc
        except OSError as exc:
            exc = _construct_local_error_msg(exc, dbx_path)
            exc.user_message_title = "Could not save file"
            logger.error("File could not be saved to local drive: {0}".format(exc),
                         exc_info=exc)
            return False

        logger.debug("File '{0}' (rev {1}) from '{2}' was successfully downloaded "
                     "as '{3}'.".format(md.name, md.rev, md.path_display, dst_path))

        return md

    def upload(self, local_path, dbx_path, chunk_size=10, **kwargs):
        """
        Uploads local file to Dropbox.

        :param str local_path: Path of local file to upload.
        :param str dbx_path: Path to save file on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_upload.
        :param int chunk_size: Maximum size for individual uploads in MB. If
            the file size exceeds the chunk_size, an upload-session is created
            instead.
        :returns: Metadata of uploaded file or `False` if upload failed.
        """

        file_size = osp.getsize(local_path)
        chunk_size = int(tobytes(chunk_size, "MB"))

        mtime = osp.getmtime(local_path)
        mtime_dt = datetime.datetime(*time.gmtime(mtime)[:6])

        try:
            with open(local_path, "rb") as f:
                if file_size <= chunk_size:
                    md = self.dbx.files_upload(
                            f.read(), dbx_path, client_modified=mtime_dt, **kwargs)
                else:
                    session_start = self.dbx.files_upload_session_start(
                        f.read(chunk_size))
                    cursor = dropbox.files.UploadSessionCursor(
                        session_id=session_start.session_id, offset=f.tell())
                    commit = dropbox.files.CommitInfo(
                            path=dbx_path, client_modified=mtime_dt, **kwargs)

                    while f.tell() < file_size:
                        if file_size - f.tell() <= chunk_size:
                            md = self.dbx.files_upload_session_finish(
                                f.read(chunk_size), cursor, commit)
                        else:
                            self.dbx.files_upload_session_append_v2(
                                f.read(chunk_size), cursor)
                            cursor.offset = f.tell()
        except (dropbox.exceptions.ApiError, OSError) as exc:
            raise _to_maestral_error(exc, dbx_path) from exc

        logger.debug("File '{0}' (rev {1}) uploaded to Dropbox.".format(
            md.path_display, md.rev))

        return md

    def remove(self, dbx_path, **kwargs):
        """
        Removes file / folder from Dropbox.

        :param str dbx_path: Path to file on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_delete.
        :returns: Metadata of deleted file or ``False`` if the file does not exist on
            Dropbox.
        :raises: :class:`MaestralApiError` if deletion fails for any other reason than
            a non-existing file.
        """
        try:
            # try to move file (response will be metadata, probably)
            md = self.dbx.files_delete(dbx_path, **kwargs)
        except dropbox.exceptions.ApiError as exc:
            if exc.error.is_path_lookup():
                # don't log as error if file did not exist
                logger.debug("An error occurred when deleting '{0}': the file does "
                             "not exist on Dropbox".format(dbx_path))
                return True
            else:
                raise _to_maestral_error(exc, dbx_path) from exc

        logger.debug("File / folder '{0}' removed from Dropbox.".format(dbx_path))

        return md

    def move(self, dbx_path, new_path):
        """
        Moves/renames files or folders on Dropbox.

        :param str dbx_path: Path to file/folder on Dropbox.
        :param str new_path: New path on Dropbox to move to.
        :returns: Metadata of moved file/folder.
        :raises: :class:`MaestralApiError`
        """
        try:
            md = self.dbx.files_move(dbx_path, new_path, allow_shared_folder=True,
                                     allow_ownership_transfer=True)
        except dropbox.exceptions.ApiError as exc:
            raise _to_maestral_error(exc, new_path) from exc

        logger.debug("File moved from '{0}' to '{1}' on Dropbox.".format(
                     dbx_path, md.path_display))

        return md

    def make_dir(self, dbx_path, **kwargs):
        """
        Creates folder on Dropbox.

        :param str dbx_path: Path o fDropbox folder.
        :param kwargs: Keyword arguments for Dropbox SDK files_create_folder.
        :returns: Metadata of created folder.
        :raises: :class:`MaestralApiError`
        """
        try:
            md = self.dbx.files_create_folder(dbx_path, **kwargs)
        except dropbox.exceptions.ApiError as exc:
            raise _to_maestral_error(exc, dbx_path) from exc

        logger.debug("Created folder '%s' on Dropbox.", md.path_display)

        return md

    def list_folder(self, dbx_path, retry=3, include_non_downloadable_files=False,
                    **kwargs):
        """
        Lists contents of a folder on Dropbox as dictionary mapping unicode
        file names to FileMetadata|FolderMetadata entries.

        :param str dbx_path: Path of folder on Dropbox.
        :param int retry: Number of times to try again call fails because cursor is
            reset. Defaults to 3.
        :param bool include_non_downloadable_files: If ``True``, files that cannot be
            downloaded (at the moment only G-suite files on Dropbox) will be included.
            Defaults to ``False``.
        :param kwargs: Other keyword arguments for Dropbox SDK files_list_folder.
        :returns: :class:`dropbox.files.ListFolderResult` instance.
        :rtype: :class:`dropbox.files.ListFolderResult`
        :raises: :class:`MaestralApiError`
        """

        results = []

        try:
            res = self.dbx.files_list_folder(
                dbx_path,
                include_non_downloadable_files=include_non_downloadable_files,
                **kwargs
            )
            results.append(res)
        except dropbox.exceptions.ApiError as exc:
            raise _to_maestral_error(exc, dbx_path) from exc

        idx = 0

        while results[-1].has_more:
            idx += len(results[-1].entries)
            logger.info("Indexing %s..." % idx)
            try:
                more_results = self.dbx.files_list_folder_continue(results[-1].cursor)
                results.append(more_results)
            except dropbox.exceptions.ApiError as exc:
                new_exc = _to_maestral_error(exc, dbx_path)
                if isinstance(new_exc, CursorResetError) and self._retry_count < retry:
                    # retry up to three times, then raise
                    self._retry_count += 1
                    self.list_folder(dbx_path, include_non_downloadable_files, **kwargs)
                else:
                    self._retry_count = 0
                    raise new_exc from exc

        logger.debug("Listed contents of folder '{0}'".format(dbx_path))

        self._retry_count = 0

        return self.flatten_results(results)

    @staticmethod
    def flatten_results(results):
        """
        Flattens a list of :class:`dropbox.files.ListFolderResult` instances
        and returns their entries only. Only the last cursor will be kept.

        :param list results: List of :class:`dropbox.files.ListFolderResult`
            instances.
        :returns: Single :class:`dropbox.files.ListFolderResult` instance.
        :rtype: :class:`dropbox.files.ListFolderResult`
        """
        entries_all = []
        for result in results:
            entries_all += result.entries
        results_flattened = dropbox.files.ListFolderResult(
            entries=entries_all, cursor=results[-1].cursor, has_more=False)

        return results_flattened

    def wait_for_remote_changes(self, last_cursor, timeout=40):
        """
        Waits for remote changes since :param:`last_cursor`. Call this method
        after starting the Dropbox client and periodically to get the latest
        updates.

        :param str last_cursor: Last to cursor to compare for changes.
        :param int timeout: Seconds to wait until timeout. Must be between 30 and 480.
        :returns: ``True`` if changes are available, ``False`` otherwise.
        :rtype: bool
        :raises: :class:`MaestralApiError`
        """

        if not 30 <= timeout <= 480:
            raise ValueError("Timeout must be in range [30, 480]")

        logger.debug("Waiting for remote changes since cursor:\n{0}".format(last_cursor))

        # honour last request to back off
        if self._last_longpoll is not None:
            while time.time() - self._last_longpoll < self._backoff:
                time.sleep(1)

        try:
            result = self.dbx.files_list_folder_longpoll(last_cursor, timeout=timeout)
        except dropbox.exceptions.ApiError as exc:
            raise _to_maestral_error(exc) from exc

        # keep track of last long poll, back off if requested by SDK
        if result.backoff:
            self._backoff = result.backoff + 5
        else:
            self._backoff = 0

        self._last_longpoll = time.time()

        return result.changes  # will be True or False

    def list_remote_changes(self, last_cursor):
        """
        Lists changes to remote Dropbox since :param:`last_cursor`. Call this
        after :method:`wait_for_remote_changes` returns `True`. Only remote changes
        in currently synced folders will be returned by default.

        :param str last_cursor: Last to cursor to compare for changes.
        :returns: :class:`dropbox.files.ListFolderResult` instance.
        :rtype: :class:`dropbox.files.ListFolderResult`
        :raises:
        """

        results = []

        try:
            results.append(self.dbx.files_list_folder_continue(last_cursor))
        except dropbox.exceptions.ApiError as exc:
            raise _to_maestral_error(exc) from exc

        while results[-1].has_more:
            try:
                more_results = self.dbx.files_list_folder_continue(results[-1].cursor)
                results.append(more_results)
            except dropbox.exceptions.ApiError as exc:
                raise _to_maestral_error(exc) from exc

        # combine all results into one
        results = self.flatten_results(results)

        logger.debug("Listed remote changes")

        return results


class MaestralApiError(Exception):

    def __init__(self, title, message, dbx_path=None, dbx_path_dst=None,
                 local_path=None, local_path_dst=None):
        super().__init__(title)
        self.title = title
        self.message = message
        self.dbx_path = dbx_path
        self.dbx_path_dst = dbx_path_dst
        self.local_path = local_path
        self.local_path_dst = local_path_dst


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


def _construct_local_error_msg(exc, dbx_path=None):
    """
    Gets the OSError and tries to add a reasonably informative error message.

    :param exc: Python Exception.
    :returns: :class:`MaestralApiError` instance.
    :rtype: :class:`MaestralApiError`
    """
    title = exc.args[0]
    if isinstance(exc, PermissionError):
        text = "Insufficient read or write permissions for this location."
    elif isinstance(exc, FileNotFoundError):
        text = "The given path does not exist."
    else:
        text = None

    err = MaestralApiError(title, text, dbx_path)

    return err


# TODO: improve checks for non-downloadable files
def _to_maestral_error(exc, dbx_path=None, local_path=None):
    """
    Gets the Dropbox API Error and tries to add a reasonably informative error
    message from the mess which is the Python Dropbox SDK exception handling.

    :param exc: :class:`dropbox.exceptions.ApiError` instance.
    :returns: :class:`MaestralApiError` instance.
    :rtype: :class:`MaestralApiError`
    """

    err_type = MaestralApiError

    # ----------------------- Dropbox API Errors -----------------------------------------
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
                    lookup_error = error.get_from_lookup()
                    text, err_type = _get_lookup_error_msg(lookup_error)
                elif error.is_path_write():
                    write_error = error.get_from_write()
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
                    "developer with the information shown below.")

    # ----------------------- Local read / write errors ----------------------------------
    elif isinstance(exc, PermissionError):
        title = "Could not download file"
        text = "Insufficient read or write permissions for the download location."
        err_type = InsufficientPermissionsError
    elif isinstance(exc, FileNotFoundError):
        title = "Could not download file"
        text = "The given download path is invalid."
        err_type = PathError

    # ----------------------- Authentication errors --------------------------------------
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

    # -------------------------- Everything else -----------------------------------------
    else:
        title = exc.arg[0]
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
