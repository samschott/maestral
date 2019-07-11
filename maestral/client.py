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

import dropbox
from dropbox import DropboxOAuth2FlowNoRedirect

from maestral.config.main import CONF, SUBFOLDER
from maestral.config.base import get_conf_path

logger = logging.getLogger(__name__)
# create single requests session for all clients
SESSION = dropbox.dropbox.create_session()

APP_KEY = "2jmbq42w7vof78h"
APP_SECRET = "lrsxo47dvuulex5"


def tobytes(value, unit, bsize=1024):
    """
    Convert size from megabytes to bytes.

    :param int value: Value in bytes.
    :param str unit: Unit to convert to. 'KB' to 'EB' are supported.
    :param int bsize: Conversion between bytes and next higher unit.
    :return: Converted value in units of `to`.
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
    :return: Converted value in units of `to`.
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
    auth_flow = None
    oAuth2FlowResult = None
    access_token = ""
    account_id = ""
    user_id = ""

    def __init__(self, app_key=APP_KEY, app_secret=APP_SECRET):
        self.app_key = app_key
        self.app_secret = app_secret

        # prepare auth flow
        self.auth_flow = DropboxOAuth2FlowNoRedirect(self.app_key, self.app_secret)
        self.load_creds()

    def link(self):
        authorize_url = self.auth_flow.start()
        print("1. Go to: " + authorize_url)
        print("2. Click \"Allow\" (you might have to log in first).")
        print("3. Copy the authorization code.")
        auth_code = input("Enter the authorization code here: ").strip()

        try:
            self.oAuth2FlowResult = self.auth_flow.finish(auth_code)
            self.access_token = self.oAuth2FlowResult.access_token
            self.account_id = self.oAuth2FlowResult.account_id
            self.user_id = self.oAuth2FlowResult.user_id
        except Exception as e:
            logger.error(e)
            return

        self.write_creds()

    def load_creds(self):
        print(" > Loading access token..."),
        try:
            with open(self.TOKEN_FILE) as f:
                stored_creds = f.read()
            self.access_token, self.account_id, self.user_id = stored_creds.split("|")
            print(" [OK]")
        except IOError:
            print(" [FAILED]")
            print(" x Access token not found. Beginning new session.")
            self.link()

    def write_creds(self):
        with open(self.TOKEN_FILE, "w+") as f:
            f.write("|".join([self.access_token, self.account_id, self.user_id]))

        print(" > Credentials written.")

    def delete_creds(self):
        os.unlink(self.TOKEN_FILE)
        print(" > Credentials removed.")


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
        self.last_longpoll = None
        self.backoff = 0

        # initialize API client
        self.dbx = dropbox.Dropbox(self.auth.access_token, session=SESSION)
        print(" > MaestralClient is ready.")

    def get_account_info(self):
        """
        Gets current account information.

        :return: :class:`dropbox.users.FullAccount` instance or `None` if failed.
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

        CONF.set("account", "email", res.email)
        CONF.set("account", "type", account_type)

        return res

    def get_space_usage(self):
        """
        Gets current account space usage.

        :return: :class:`SpaceUsage` instance or `False` if failed.
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
        :return: FileMetadata|FolderMetadata entries or `False` if failed.
        """

        try:
            md = self.dbx.files_get_metadata(dbx_path, **kwargs)
            logger.debug("Retrieved metadata for '{0}'".format(md.path_display))
        except dropbox.exceptions.ApiError as err:
            # API error is only raised when the path does not exist on Dropbox
            # this is handled on a DEBUG level since we use call `get_metadata` to check
            # if a file exists
            logging.debug("Could not get metadata for '%s': %s", dbx_path, err)
            md = False

        return md

    def download(self, dbx_path, dst_path, **kwargs):
        """
        Downloads file from Dropbox to our local folder.

        :param str dbx_path: Path to file on Dropbox.
        :param str dst_path: Path to download destination.
        :param kwargs: Keyword arguments for Dropbox SDK files_download_to_file.
        :return: :class:`FileMetadata` or
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
        except dropbox.exceptions.ApiError as err:
            logger.error("An error occurred while downloading '{0}' file to '{1}': "
                         "{2}.".format(dbx_path, dst_path, err))
            return False
        except (IOError, OSError) as err:
            logger.error("File could not be saved to local drive: {0}".format(err))
            return False

        logger.debug("File '{0}' (rev {1}) from '{2}' was successfully downloaded "
                     "as '{3}'.\n".format(md.name, md.rev, md.path_display, dst_path))

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
        :return: Metadata of uploaded file or `False` if upload failed.
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
        except dropbox.exceptions.ApiError as err:
            logger.error("An error occurred while uploading file '{0}': {1}.".format(
                local_path, err))
            return False
        except (IOError, OSError) as err:
            logger.error("File could read local file: {0}".format(err))
            return False

        logger.debug("File '{0}' (rev {1}) uploaded to Dropbox.".format(
            md.path_display, md.rev))

        return md

    def remove(self, dbx_path, **kwargs):
        """
        Removes file / folder from Dropbox.

        :param str dbx_path: Path to file on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_delete.
        :return: Metadata of deleted file or ``False`` if the file does not exist on
            Dropbox.

        :raises: Raises :class:`dropbox.exceptions.ApiError` if deletion fails for any
            other reason than a non-existing file.
        """
        try:
            # try to move file (response will be metadata, probably)
            md = self.dbx.files_delete(dbx_path, **kwargs)
        except dropbox.exceptions.ApiError as err:
            if err.error.is_path_lookup():
                # don't log as error if file did not exist
                logger.debug("An error occurred when deleting '{0}': the file does "
                             "not exist on Dropbox".format(dbx_path))
                return True
            else:
                logger.error("An error occurred when deleting '{0}': {1}".format(
                    dbx_path, err))
                return False

        logger.debug("File / folder '{0}' removed from Dropbox.".format(dbx_path))

        return md

    def move(self, dbx_path, new_path):
        """
        Moves/renames files or folders on Dropbox.

        :param str dbx_path: Path to file/folder on Dropbox.
        :param str new_path: New path on Dropbox to move to.
        :return: Metadata of moved file/folder or ``False`` if move failed.
        """
        try:
            md = self.dbx.files_move(dbx_path, new_path, allow_shared_folder=True,
                                     allow_ownership_transfer=True)
        except dropbox.exceptions.ApiError as err:
            logger.error("An error occurred when moving '{0}' to '{1}': {2}".format(
                    dbx_path, new_path, err))
            return False

        logger.debug("File moved from '{0}' to '{1}' on Dropbox.".format(
                     dbx_path, md.path_display))

        return md

    def make_dir(self, dbx_path, **kwargs):
        """
        Creates folder on Dropbox.

        :param str dbx_path: Path o fDropbox folder.
        :param kwargs: Keyword arguments for Dropbox SDK files_create_folder.
        :return: Metadata of created folder or ``False`` if failed.
        """
        try:
            md = self.dbx.files_create_folder(dbx_path, **kwargs)
        except dropbox.exceptions.ApiError as err:
            logger.error("An error occurred creating dir '{0}': {1}".format(dbx_path, err))
            return False

        logger.debug("Created folder '%s' on Dropbox.", md.path_display)

        return md

    def list_folder(self, dbx_path, **kwargs):
        """
        Lists contents of a folder on Dropbox as dictionary mapping unicode
        file names to FileMetadata|FolderMetadata entries.

        :param str dbx_path: Path of folder on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_list_folder.
        :return: :class:`dropbox.files.ListFolderResult` instance or ``False`` if failed.
        :rtype: :class:`dropbox.files.ListFolderResult`
        """

        results = []

        try:
            results.append(self.dbx.files_list_folder(dbx_path, **kwargs))
        except dropbox.exceptions.ApiError as err:
            logger.debug("Folder listing failed for '%s': %s", dbx_path, err)
            return False

        idx = 0

        while results[-1].has_more:
            idx += len(results[-1].entries)
            logger.info("Indexing %s..." % idx)
            try:
                more_results = self.dbx.files_list_folder_continue(results[-1].cursor)
                results.append(more_results)
            except dropbox.exceptions.ApiError as err:
                logger.error("Folder listing failed for '{0}': {1}".format(dbx_path, err))
                return False

        logger.debug("Listed contents of folder '{0}'".format(dbx_path))

        return self.flatten_results(results)

    @staticmethod
    def flatten_results(results):
        """
        Flattens a list of :class:`dropbox.files.ListFolderResult` instances
        and returns their entries only. Only the last cursor will be kept.

        :param list results: List of :class:`dropbox.files.ListFolderResult`
            instances.
        :return: Single :class:`dropbox.files.ListFolderResult` instance.
        :rtype: :class:`dropbox.files.ListFolderResult`

        """
        entries_all = []
        for result in results:
            entries_all += result.entries
        results_flattened = dropbox.files.ListFolderResult(
            entries=entries_all, cursor=results[-1].cursor, has_more=False)

        return results_flattened

    def wait_for_remote_changes(self, last_cursor, timeout=20):
        """
        Waits for remote changes since :param:`last_cursor`. Call this method
        after starting the Dropbox client and periodically to get the latest
        updates.

        :param str last_cursor: Last to cursor to compare for changes.
        :param int timeout: Seconds to wait until timeout.
        :return: ``True`` if changes are available, ``False`` otherwise.
        :rtype: bool
        """

        logger.debug("Waiting for remote changes since cursor:\n{0}".format(last_cursor))

        # honour last request to back off
        if self.last_longpoll is not None:
            while time.time() - self.last_longpoll < self.backoff:
                time.sleep(1)

        try:
            result = self.dbx.files_list_folder_longpoll(last_cursor, timeout=timeout)
        except dropbox.exceptions.ApiError as e:
            if e.error.is_reset():
                logger.warning("Cursor has been invalidated. Please try again.")
            else:
                logger.error("Could not get remote changes: {0}".format(e))
            return False

        # keep track of last long poll, back off if requested by SDK
        if result.backoff:
            self.backoff = result.backoff + 5
        else:
            self.backoff = 0

        self.last_longpoll = time.time()

        return result.changes

    def list_remote_changes(self, last_cursor):
        """
        Lists changes to remote Dropbox since :param:`last_cursor`. Call this
        after :method:`wait_for_remote_changes` returns `True`. Only remote changes
        in currently synced folders will be returned by default.

        :param str last_cursor: Last to cursor to compare for changes.

        :return: :class:`dropbox.files.ListFolderResult` instance or False if
            requests failed.
        :rtype: :class:`dropbox.files.ListFolderResult`
        """

        results = []

        try:
            results.append(self.dbx.files_list_folder_continue(last_cursor))
        except dropbox.exceptions.ApiError as err:
            logging.warning("Folder listing failed: %s", err)
            return False

        while results[-1].has_more:
            try:
                more_results = self.dbx.files_list_folder_continue(results[-1].cursor)
                results.append(more_results)
            except dropbox.exceptions.ApiError as err:
                logging.warning("Folder listing failed: %s", err)
                return False

        # combine all results into one
        results = self.flatten_results(results)

        logger.debug("Listed remote changes")

        return results
