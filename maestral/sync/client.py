# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# system imports
import os
import os.path as osp
import time
import datetime
import logging
import functools

# external packages
import requests
import dropbox

# maestral modules
from maestral.sync.oauth import OAuth2Session
from maestral.config.main import MaestralConfig
from maestral.sync.errors import api_to_maestral_error, os_to_maestral_error
from maestral.sync.errors import CursorResetError
from maestral import __version__


logger = logging.getLogger(__name__)

# create single requests session for all clients
SESSION = dropbox.dropbox.create_session()
_major_minor_version = ".".join(__version__.split(".")[:2])
USER_AGENT = f"Maestral/v{_major_minor_version}"


CONNECTION_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.HTTPError,
    requests.exceptions.ReadTimeout,
    requests.exceptions.RetryError,
    ConnectionError,
)


OS_FILE_ERRORS = (
    FileExistsError,
    FileNotFoundError,
    InterruptedError,
    IsADirectoryError,
    NotADirectoryError,
    PermissionError,
)


def bytes_to_str(num, suffix='B'):
    """
    Convert number to a human readable string with decimal prefix.

    :param float num: Value in given unit.
    :param str suffix: Unit suffix. Defaults to 'B'.
    :returns: Human readable string with decimal prefixes.
    :rtype: str
    """
    for unit in ['','K','M','G']:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}T{suffix}"


class SpaceUsage(dropbox.users.SpaceUsage):

    def allocation_type(self):
        if self.allocation.is_team():
            return "team"
        elif self.allocation.is_individual():
            return "individual"
        else:
            return ""

    def __str__(self):

        if self.allocation.is_individual():
            used = self.used
            allocated = self.allocation.get_individual().allocated
        elif self.allocation.is_team():
            used = self.allocation.get_team().used
            allocated = self.allocation.get_team().allocated
        else:
            return bytes_to_str(self.used)

        percent = used / allocated
        return f"{percent:.1%} of {bytes_to_str(allocated)} used"


def to_maestral_error(dbx_path_arg=None, local_path_arg=None):
    """
    Decorator that converts all OS_FILE_ERRORS and DropboxExceptions to MaestralApiErrors.

    :param int dbx_path_arg: Argument number to take as dbx_path for exception.
    :param int local_path_arg: Argument number to take as local_path_arg for exception.
    """

    def decorator(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):

            dbx_path = args[dbx_path_arg] if dbx_path_arg else None
            local_path = args[local_path_arg] if local_path_arg else None

            try:
                return func(*args, **kwargs)
            except dropbox.exceptions.DropboxException as exc:
                raise api_to_maestral_error(exc, dbx_path, local_path)
            except OS_FILE_ERRORS as exc:
                raise os_to_maestral_error(exc, dbx_path, local_path)
            except CONNECTION_ERRORS:
                raise ConnectionError("Cannot connect to Dropbox")

        return wrapper

    return decorator


class MaestralApiClient(object):
    """Client for Dropbox SDK.

    This client defines basic methods to wrap Dropbox Python SDK calls, such as creating,
    moving, modifying and deleting files and folders on Dropbox and downloading files from
    Dropbox.

    All Dropbox API errors are caught and handled here. ConnectionErrors will
    be caught and handled by :class:`MaestralMonitor` instead.

    :param int timeout: Timeout for individual requests in sec. Defaults to 60 sec.
    """

    SDK_VERSION = "2.0"
    _timeout = 60

    def __init__(self, config_name='maestral', timeout=_timeout):

        self._conf = MaestralConfig(config_name)

        # get Dropbox session
        self.auth = OAuth2Session(config_name)
        if not self.auth.load_token():
            self.auth.link()
        self._timeout = timeout
        self._last_longpoll = None
        self._backoff = 0
        self._retry_count = 0

        # initialize API client
        self.dbx = dropbox.Dropbox(
            self.auth.access_token,
            session=SESSION,
            user_agent=USER_AGENT,
            timeout=self._timeout
        )

    @to_maestral_error()
    def get_account_info(self, dbid=None):
        """
        Gets current account information.

        :param str dbid: Dropbox ID of account. If not given, will get the info of our own
            account.
        :returns: :class:`dropbox.users.FullAccount` instance or `None` if failed.
        :rtype: dropbox.users.FullAccount
        """
        if dbid:
            res = self.dbx.users_get_account(dbid)
        else:
            res = self.dbx.users_get_current_account()

        if not dbid:
            # save our own account info to config
            if res.account_type.is_basic():
                account_type = "basic"
            elif res.account_type.is_business():
                account_type = "business"
            elif res.account_type.is_pro():
                account_type = "pro"
            else:
                account_type = ""

            self._conf.set("account", "account_id", res.account_id)
            self._conf.set("account", "email", res.email)
            self._conf.set("account", "display_name", res.name.display_name)
            self._conf.set("account", "abbreviated_name", res.name.abbreviated_name)
            self._conf.set("account", "type", account_type)

        return res

    @to_maestral_error()
    def get_space_usage(self):
        """
        Gets current account space usage.

        :returns: :class:`SpaceUsage` instance or `False` if failed.
        :rtype: SpaceUsage
        """
        res = self.dbx.users_get_space_usage()

        # convert from dropbox.users.SpaceUsage to SpaceUsage
        res.__class__ = SpaceUsage

        # save results to config
        self._conf.set("account", "usage", str(res))
        self._conf.set("account", "usage_type", res.allocation_type())

        return res

    @to_maestral_error()
    def unlink(self):
        """
        Unlinks the Dropbox account and deletes local sync information.
        """
        self.auth.delete_creds()
        self.dbx.auth_token_revoke()  # should only raise auth errors

    @to_maestral_error(dbx_path_arg=1)
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
            logger.debug(f"Retrieved metadata for '{md.path_display}'")
        except dropbox.exceptions.ApiError as exc:
            # DropboxAPI error is only raised when the item does not exist on Dropbox
            # this is handled on a DEBUG level since we use call `get_metadata` to check
            # if a file exists
            logger.debug(f"Could not get metadata for '{dbx_path}': {exc}")
            md = False

        return md

    @to_maestral_error(dbx_path_arg=1)
    def list_revisions(self, dbx_path, mode="path", limit=10):
        """
        Lists all file revisions for the given file.

        :param str dbx_path: Path to file on Dropbox.
        :param str mode: Must be "path" or "id". If "id", specify the Dropbox file ID
            instead of the file path to get revisions across move and rename events.
            Defaults to "path".
        :param int limit: Number of revisions to list. Defaults to 10.
        :returns: :class:`dropbox.files.ListRevisionsResult` instance
        """

        mode = dropbox.files.ListRevisionsMode(mode)
        return self.dbx.files_list_revisions(dbx_path, mode=mode, limit=limit)

    @to_maestral_error(dbx_path_arg=1)
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
        # create local directory if not present
        dst_path_directory = osp.dirname(dst_path)
        try:
            os.makedirs(dst_path_directory)
        except FileExistsError:
            pass

        md = self.dbx.files_download_to_file(dst_path, dbx_path, **kwargs)

        logger.debug(f"File '{md.path_display}' (rev {md.rev}) was successfully downloaded as '{dst_path}'")

        return md

    @to_maestral_error(dbx_path_arg=2)
    def upload(self, local_path, dbx_path, chunk_size_mb=5, **kwargs):
        """
        Uploads local file to Dropbox.

        :param str local_path: Path of local file to upload.
        :param str dbx_path: Path to save file on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_upload.
        :param int chunk_size_mb: Maximum size for individual uploads in MB. Must be
            smaller than 150 MB.
        :returns: Metadata of uploaded file or `False` if upload failed.
        """

        chunk_size_mb = min(chunk_size_mb, 150)
        chunk_size = chunk_size_mb * 10**6  # convert to bytes

        file_size = osp.getsize(local_path)
        file_size_str = bytes_to_str(file_size)
        uploaded = 0

        mtime = osp.getmtime(local_path)
        mtime_dt = datetime.datetime(*time.gmtime(mtime)[:6])

        with open(local_path, "rb") as f:
            if file_size <= chunk_size:
                md = self.dbx.files_upload(
                        f.read(), dbx_path, client_modified=mtime_dt, **kwargs)
            else:
                logger.info(f"Uploading {bytes_to_str(uploaded)}/{file_size_str}...")
                session_start = self.dbx.files_upload_session_start(f.read(chunk_size))
                cursor = dropbox.files.UploadSessionCursor(
                    session_id=session_start.session_id, offset=f.tell())
                commit = dropbox.files.CommitInfo(
                        path=dbx_path, client_modified=mtime_dt, **kwargs)

                while f.tell() < file_size:
                    if file_size - f.tell() <= chunk_size:
                        md = self.dbx.files_upload_session_finish(f.read(chunk_size), cursor, commit)
                        logger.info(f"Uploading {bytes_to_str(uploaded)}/{file_size_str}...")
                    else:
                        # Note: we currently do not support resuming interrupted uploads.
                        # However, this can be achieved catching connection errors and
                        # retrying until the upload succeeds. Incorrect offsets due to
                        # a dropped package can be corrected by getting the right
                        # offset from the resulting UploadSessionOffsetError and
                        # resuming the upload from this point.
                        self.dbx.files_upload_session_append_v2(f.read(chunk_size), cursor)
                        cursor.offset = f.tell()
                        uploaded += chunk_size
                        logger.info(f"Uploading {bytes_to_str(uploaded)}/{file_size_str}...")

        logger.debug(f"File '{md.path_display}' (rev {md.rev}) uploaded to Dropbox")

        return md

    @to_maestral_error(dbx_path_arg=1)
    def remove(self, dbx_path, **kwargs):
        """
        Removes file / folder from Dropbox.

        :param str dbx_path: Path to file on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_delete_v2.
        :returns: Metadata of deleted file or ``False`` if the file does not exist on
            Dropbox.
        :raises: :class:`MaestralApiError`.
        """
        # try to move file (response will be metadata, probably)
        res = self.dbx.files_delete_v2(dbx_path, **kwargs)
        md = res.metadata

        logger.debug(f"Item '{dbx_path}' removed from Dropbox")

        return md

    @to_maestral_error(dbx_path_arg=2)
    def move(self, dbx_path, new_path, **kwargs):
        """
        Moves/renames files or folders on Dropbox.

        :param str dbx_path: Path to file/folder on Dropbox.
        :param str new_path: New path on Dropbox to move to.
        :param kwargs: Keyword arguments for Dropbox SDK files_move_v2.
        :returns: Metadata of moved file/folder.
        :raises: :class:`MaestralApiError`
        """
        res = self.dbx.files_move_v2(
            dbx_path,
            new_path,
            allow_shared_folder=True,
            allow_ownership_transfer=True,
            **kwargs
        )
        md = res.metadata

        logger.debug(f"Item moved from '{dbx_path}' to '{md.path_display}' on Dropbox")

        return md

    @to_maestral_error(dbx_path_arg=1)
    def make_dir(self, dbx_path, **kwargs):
        """
        Creates folder on Dropbox.

        :param str dbx_path: Path o fDropbox folder.
        :param kwargs: Keyword arguments for Dropbox SDK files_create_folder_v2.
        :returns: Metadata of created folder.
        :raises: :class:`MaestralApiError`
        """
        res = self.dbx.files_create_folder_v2(dbx_path, **kwargs)
        md = res.metadata

        logger.debug(f"Created folder '{md.path_display}' on Dropbox")

        return md

    @to_maestral_error(dbx_path_arg=1)
    def get_latest_cursor(self, dbx_path, include_non_downloadable_files=False, **kwargs):
        """
        Gets the latest cursor for the given folder and subfolders.

        :param str dbx_path: Path of folder on Dropbox.
        :param bool include_non_downloadable_files: If ``True``, files that cannot be
            downloaded (at the moment only G-suite files on Dropbox) will be included.
            Defaults to ``False``.
        :param kwargs: Other keyword arguments for Dropbox SDK files_list_folder.
        :returns: The latest cursor representing a state of a folder and its subfolders.
        :rtype: str
        :raises: :class:`MaestralApiError`
        """

        res = self.dbx.files_list_folder_get_latest_cursor(
            dbx_path,
            include_non_downloadable_files=include_non_downloadable_files,
            recursive=True,
            **kwargs,
        )

        return res.cursor

    @to_maestral_error(dbx_path_arg=1)
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

        res = self.dbx.files_list_folder(
            dbx_path,
            include_non_downloadable_files=include_non_downloadable_files,
            **kwargs
        )
        results.append(res)

        idx = 0

        while results[-1].has_more:
            idx += len(results[-1].entries)
            logger.info(f"Indexing {idx}...")
            try:
                more_results = self.dbx.files_list_folder_continue(results[-1].cursor)
                results.append(more_results)
            except dropbox.exceptions.DropboxException as exc:
                new_exc = api_to_maestral_error(exc, dbx_path)
                if isinstance(new_exc, CursorResetError) and self._retry_count < retry:
                    # retry up to three times, then raise
                    self._retry_count += 1
                    self.list_folder(dbx_path, include_non_downloadable_files, **kwargs)
                else:
                    self._retry_count = 0
                    raise new_exc

        logger.debug(f"Listed contents of folder '{dbx_path}'")

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

    @to_maestral_error()
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

        logger.debug(f"Waiting for remote changes since cursor:\n{last_cursor}")

        # honour last request to back off
        if self._last_longpoll is not None:
            while time.time() - self._last_longpoll < self._backoff:
                time.sleep(1)

        result = self.dbx.files_list_folder_longpoll(last_cursor, timeout=timeout)

        # keep track of last long poll, back off if requested by SDK
        if result.backoff:
            self._backoff = result.backoff + 5
        else:
            self._backoff = 0

        logger.debug(f"Detected remote changes: {result.changes}")

        self._last_longpoll = time.time()

        return result.changes  # will be True or False

    @to_maestral_error()
    def list_remote_changes(self, last_cursor):
        """
        Lists changes to remote Dropbox since :param:`last_cursor`. Call this
        after :method:`wait_for_remote_changes` returns ``True``.

        :param str last_cursor: Last to cursor to compare for changes.
        :returns: :class:`dropbox.files.ListFolderResult` instance.
        :rtype: :class:`dropbox.files.ListFolderResult`
        :raises:
        """

        results = [self.dbx.files_list_folder_continue(last_cursor)]

        while results[-1].has_more:
            more_results = self.dbx.files_list_folder_continue(results[-1].cursor)
            results.append(more_results)

        # combine all results into one
        results = self.flatten_results(results)

        logger.debug(f"Listed remote changes: {len(results.entries)} changes")

        return results
