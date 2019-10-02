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
import dropbox

# maestral modules
from maestral.sync.oauth import OAuth2Session
from maestral.config.main import CONF
from maestral.sync.errors import api_to_maestral_error, os_to_maestral_error
from maestral.sync.errors import OS_FILE_ERRORS, CursorResetError


logger = logging.getLogger(__name__)

# create single requests session for all clients
SESSION = dropbox.dropbox.create_session()
USER_AGENT = "Maestral/v0.2"


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

    def allocation_type(self):
        if self.allocation.is_team():
            return "team"
        elif self.allocation.is_individual():
            return "individual"
        else:
            return ""

    def __str__(self):

        if self.allocation.is_team():
            str_rep_usage_type = " (Team)"
        else:
            str_rep_usage_type = ""
        return self.__repr__() + str_rep_usage_type

    def __repr__(self):
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
        str_rep_usage = "{:.1f}% of {:,}GB used".format(percent, alloc_gb)
        return str_rep_usage


def to_maestral_error():
    """
    Decorator that converts all OS_FILE_ERRORS and DropboxExceptions to MaestralApiErrors.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                res = func(*args, **kwargs)
            except dropbox.exceptions.DropboxException as exc:
                raise api_to_maestral_error(exc)
            except OS_FILE_ERRORS as exc:
                raise os_to_maestral_error(exc)

            return res

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

    def __init__(self, timeout=_timeout):

        # get Dropbox session
        self.auth = OAuth2Session()
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

    def get_account_info(self, dbid=None):
        """
        Gets current account information.

        :param str dbid: Dropbox ID of account. If not given, will get the info of our own
            account.
        :returns: :class:`dropbox.users.FullAccount` instance or `None` if failed.
        :rtype: dropbox.users.FullAccount
        """
        try:
            if dbid:
                res = self.dbx.users_get_account(dbid)
            else:
                res = self.dbx.users_get_current_account()
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc)

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

            CONF.set("account", "account_id", res.account_id)
            CONF.set("account", "email", res.email)
            CONF.set("account", "display_name", res.name.display_name)
            CONF.set("account", "abbreviated_name", res.name.abbreviated_name)
            CONF.set("account", "type", account_type)

        return res

    def get_space_usage(self):
        """
        Gets current account space usage.

        :returns: :class:`SpaceUsage` instance or `False` if failed.
        :rtype: SpaceUsage
        """
        try:
            res = self.dbx.users_get_space_usage()
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc)

        # convert from dropbox.users.SpaceUsage to SpaceUsage
        res.__class__ = SpaceUsage

        # save results to config
        CONF.set("account", "usage", repr(res))
        CONF.set("account", "usage_type", res.allocation_type())

        return res

    def unlink(self):
        """
        Unlinks the Dropbox account and deletes local sync information.
        """
        self.auth.delete_creds()
        try:
            self.dbx.auth_token_revoke()  # should only raise auth errors
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc)

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
        except dropbox.exceptions.DropboxException as exc:
            # DropboxAPI error is only raised when the item does not exist on Dropbox
            # this is handled on a DEBUG level since we use call `get_metadata` to check
            # if a file exists
            logger.debug("Could not get metadata for '%s': %s", dbx_path, exc)
            md = False

        return md

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

        try:
            res = self.dbx.files_list_revisions(dbx_path, mode=mode, limit=limit)
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc, dbx_path)

        return res

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

        if not osp.exists(dst_path_directory):
            try:
                os.makedirs(dst_path_directory)
            except FileExistsError:
                pass
            except OS_FILE_ERRORS as exc:
                raise os_to_maestral_error(exc, dbx_path)

        try:
            md = self.dbx.files_download_to_file(dst_path, dbx_path, **kwargs)
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc, dbx_path)
        except OS_FILE_ERRORS as exc:
            raise os_to_maestral_error(exc, dbx_path)

        logger.debug("File '{0}' (rev {1}) from '{2}' was successfully downloaded "
                     "as '{3}'.".format(md.name, md.rev, md.path_display, dst_path))

        return md

    def upload(self, local_path, dbx_path, chunk_size_mb=5, **kwargs):
        """
        Uploads local file to Dropbox.

        :param str local_path: Path of local file to upload.
        :param str dbx_path: Path to save file on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_upload.
        :param int chunk_size_mb: Maximum size for individual uploads in MB. If
            the file size exceeds the chunk_size, an upload-session is created
            instead.
        :returns: Metadata of uploaded file or `False` if upload failed.
        """
        try:
            file_size = osp.getsize(local_path)
            chunk_size = int(tobytes(chunk_size_mb, "MB"))

            display_unit = "GB" if file_size > tobytes(1000, "MB") else "MB"
            file_size_display = int(bytesto(file_size, display_unit))
            chunk_size_display = int(bytesto(chunk_size, display_unit))
            uploaded_display = 0

            mtime = osp.getmtime(local_path)
            mtime_dt = datetime.datetime(*time.gmtime(mtime)[:6])

            with open(local_path, "rb") as f:
                if file_size <= chunk_size:
                    md = self.dbx.files_upload(
                            f.read(), dbx_path, client_modified=mtime_dt, **kwargs)
                else:
                    logger.info("Uploading {0}/{1}{2}...".format(
                        uploaded_display, file_size_display, display_unit))
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
                            logger.info("Uploading {0}/{1}{2}...".format(
                                file_size_display, file_size_display, display_unit))
                        else:
                            self.dbx.files_upload_session_append_v2(
                                f.read(chunk_size), cursor)
                            cursor.offset = f.tell()
                            uploaded_display += chunk_size_display
                            logger.info("Uploading {0}/{1}{2}...".format(
                                uploaded_display, file_size_display, display_unit))
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc, dbx_path)
        except OS_FILE_ERRORS as exc:
            raise os_to_maestral_error(exc, dbx_path)

        logger.debug("File '{0}' (rev {1}) uploaded to Dropbox.".format(
            md.path_display, md.rev))

        return md

    def remove(self, dbx_path, **kwargs):
        """
        Removes file / folder from Dropbox.

        :param str dbx_path: Path to file on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_delete_v2.
        :returns: Metadata of deleted file or ``False`` if the file does not exist on
            Dropbox.
        :raises: :class:`MaestralApiError`.
        """
        try:
            # try to move file (response will be metadata, probably)
            res = self.dbx.files_delete_v2(dbx_path, **kwargs)
            md = res.metadata
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc, dbx_path)

        logger.debug("File / folder '{0}' removed from Dropbox.".format(dbx_path))

        return md

    def move(self, dbx_path, new_path, **kwargs):
        """
        Moves/renames files or folders on Dropbox.

        :param str dbx_path: Path to file/folder on Dropbox.
        :param str new_path: New path on Dropbox to move to.
        :param kwargs: Keyword arguments for Dropbox SDK files_move_v2.
        :returns: Metadata of moved file/folder.
        :raises: :class:`MaestralApiError`
        """
        try:
            res = self.dbx.files_move_v2(dbx_path, new_path, allow_shared_folder=True,
                                         allow_ownership_transfer=True, **kwargs)
            md = res.metadata
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc, new_path)

        logger.debug("File moved from '{0}' to '{1}' on Dropbox.".format(
                     dbx_path, md.path_display))

        return md

    def make_dir(self, dbx_path, **kwargs):
        """
        Creates folder on Dropbox.

        :param str dbx_path: Path o fDropbox folder.
        :param kwargs: Keyword arguments for Dropbox SDK files_create_folder_v2.
        :returns: Metadata of created folder.
        :raises: :class:`MaestralApiError`
        """
        try:
            res = self.dbx.files_create_folder_v2(dbx_path, **kwargs)
            md = res.metadata
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc, dbx_path)

        logger.debug("Created folder '%s' on Dropbox.", md.path_display)

        return md

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

        try:
            res = self.dbx.files_list_folder_get_latest_cursor(
                dbx_path, include_non_downloadable_files=include_non_downloadable_files,
                recursive=True,
                **kwargs)
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc, dbx_path)

        return res.cursor

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
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc, dbx_path)

        idx = 0

        while results[-1].has_more:
            idx += len(results[-1].entries)
            logger.info("Indexing {0}...".format(idx))
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
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc)

        # keep track of last long poll, back off if requested by SDK
        if result.backoff:
            self._backoff = result.backoff + 5
        else:
            self._backoff = 0

        logger.debug("Detected remote changes: {}.".format(str(result.changes)))

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
        except dropbox.exceptions.DropboxException as exc:
            raise api_to_maestral_error(exc)

        while results[-1].has_more:
            try:
                more_results = self.dbx.files_list_folder_continue(results[-1].cursor)
                results.append(more_results)
            except dropbox.exceptions.DropboxException as exc:
                raise api_to_maestral_error(exc)

        # combine all results into one
        results = self.flatten_results(results)

        logger.debug("Listed remote changes: {} changes.".format(len(results.entries)))

        return results
