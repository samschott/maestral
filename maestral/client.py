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
import itertools
import logging
from concurrent.futures import ThreadPoolExecutor
import threading
import shutil

import umsgpack
from tqdm import tqdm
import dropbox
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata
from dropbox import DropboxOAuth2FlowNoRedirect

from maestral.config.main import CONF, SUBFOLDER
from maestral.config.base import get_conf_path
from maestral.utils.notify import Notipy
from maestral.utils.content_hasher import DropboxContentHasher

logger = logging.getLogger(__name__)
# create single requests session for all clients
SESSION = dropbox.dropbox.create_session()

APP_KEY = "2jmbq42w7vof78h"
APP_SECRET = "lrsxo47dvuulex5"

REV_FILE = ".dropbox"


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


def path_exists_case_insensitive(path, root="/"):
    """
    Checks if a `path` exists in given `root` directory, similar to
    `os.path.exists` but case-insensitive. If there are multiple
    case-insensitive matches, the first one is returned. If there is no match,
    an empty string is returned.

    :param str path: Relative path of file/folder to find in the `root`
        directory.
    :param str root: Directory where we will look for `path`.
    :return: Absolute and case-sensitive path to search result on hard drive.
    :rtype: str
    """

    if not osp.isdir(root):
        raise ValueError("'{0}' is not a directory.".format(root))

    if path in ["", "/"]:
        return root

    path_list = path.lstrip(osp.sep).split(osp.sep)
    path_list_lower = [x.lower() for x in path_list]

    i = 0
    for root, dirs, files in os.walk(root):
        for d in list(dirs):
            if not d.lower() == path_list_lower[i]:
                dirs.remove(d)
        for f in list(files):
            if not f.lower() == path_list_lower[i]:
                files.remove(f)

        local_paths = [osp.join(root, name) for name in dirs + files]

        i += 1
        if i == len(path_list_lower):
            break

    if len(local_paths) == 0:
        return ''
    else:
        return local_paths[0]


def get_local_hash(dst_path):
    """
    Computes content hash of local file.

    :param str dst_path: Path to local file.
    :return: content hash to compare with ``content_hash`` attribute of
        :class:`dropbox.files.FileMetadata` object.
    """

    hasher = DropboxContentHasher()

    with open(dst_path, 'rb') as f:
        while True:
            chunk = f.read(1024)
            if len(chunk) == 0:
                break
            hasher.update(chunk)

    return hasher.hexdigest()


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

    def unlink(self):
        self.delete_creds()
        # can't unlink the app properly (API limitation), so let's just remove the token


# noinspection PyDeprecation
class MaestralClient(object):
    """Client for Dropbox SDK.

    This client defines basic methods to edit the remote Dropbox folder: it
    supports creating, moving, modifying and deleting files and folders on
    Dropbox. It also provides a method to download a file from Dropbox to a
    given local path.

    Higher level methods provide ways to list the contents of and download
    entire folder from Dropbox.

    MaestralClient also provides methods to wait for and apply changes from the
    remote Dropbox. Detecting local changes is handled by :class:`MaestralMonitor`
    instead.

    All Dropbox API errors are caught and handled here. ConnectionErrors will
    be caught and handled by :class:`MaestralMonitor` instead.

    :ivar last_cursor: Last cursor from Dropbox which was synced. The value
        is updated and saved to config file on every successful sync.
    :ivar excluded_files: List containing all files excluded from sync.
        This only contains system files such as '.DS_STore' and internal files
        such as '.dropbox' and should not be changed.
    :ivar excluded_folders: List containing all files excluded from sync.
        When adding and removing entries, make sure to update the config file
        as well so that changes persist across sessions.
    :ivar rev_file: Path of local file with rev number. This defaults
        to '/dropbox_path/.dropbox'
    :ivar dropbox_path: Path to local Dropbox folder, as loaded from config
        file. Before changing :ivar`dropbox_path`, make sure that all syncing
        is paused. Make sure to move the local Dropbox directory before
        resuming the sync and to save the new :ivar`dropbox_path` to the
        config file.
    """

    SDK_VERSION = "2.0"

    excluded_files = CONF.get("main", "excluded_files")
    excluded_folders = CONF.get("main", "excluded_folders")
    last_cursor = CONF.get("internal", "cursor")

    dbx = None
    auth = None
    dropbox_path = ''

    notify = Notipy()
    lock = threading.RLock()

    _rev_lock = threading.Lock()

    def __init__(self):

        # get Dropbox session
        self.auth = OAuth2Session()
        self.last_longpoll = None
        self.backoff = 0

        # initialize API client
        self.dbx = dropbox.Dropbox(self.auth.access_token, session=SESSION)
        print(" > MaestralClient is ready.")

        # get correct directories
        self.dropbox_path = CONF.get("main", "path")

    @property
    def rev_file(self):
        return osp.join(self.dropbox_path, REV_FILE)

    def to_dbx_path(self, local_path):
        """
        Converts a local path to a path relative to the Dropbox folder.

        :param str local_path: Full path to file in local Dropbox folder.
        :return: Relative path with respect to Dropbox folder.
        :rtype: str
        :raises ValueError: If no path is specified or path is outside of local
            Dropbox folder.
        """

        if not local_path:
            raise ValueError("No path specified.")

        dbx_root_list = osp.normpath(self.dropbox_path).split(osp.sep)
        path_list = osp.normpath(local_path).split(osp.sep)

        # Work out how much of the file path is shared by dropbox_path and path.
        i = len(osp.commonprefix([dbx_root_list, path_list]))

        if i == len(path_list):  # path corresponds to dropbox_path
            return "/"
        elif not i == len(dbx_root_list):  # path is outside of to dropbox_path
            raise ValueError("Specified path '%s' is not in Dropbox directory." % local_path)

        relative_path = "/" + "/".join(path_list[i:])

        return relative_path

    def to_local_path(self, dbx_path):
        """
        Converts a Dropbox folder to the corresponding local path.

        The `path_display` attribute returned by the Dropbox API only
        guarantees correct casing of the basename (file name or folder name)
        and not of the full path. This is because Dropbox itself is not case
        sensitive and stores all paths in lowercase internally.

        Therefore, if the parent directory is already present on the local
        drive, it's casing is used. Otherwise, the casing given by the Dropbox
        API metadata is used. This aims to preserve the correct casing as
        uploaded to Dropbox and prevents the creation of duplicate folders
        with different casing on the local drive.

        :param str dbx_path: Path to file relative to Dropbox folder.
        :return: Corresponding local path on drive.
        :rtype: str
        :raises ValueError: If no path is specified.
        """

        if not dbx_path:
            raise ValueError("No path specified.")

        dbx_path = dbx_path.replace("/", osp.sep)
        dbx_path_parent, dbx_path_basename,  = osp.split(dbx_path)

        local_parent = path_exists_case_insensitive(dbx_path_parent, self.dropbox_path)

        if local_parent == "":
            return osp.join(self.dropbox_path, dbx_path.lstrip(osp.sep))
        else:
            return osp.join(local_parent, dbx_path_basename)

    def get_rev_dict(self):
        """
        Returns dictionary of file / folder paths with rev numbers.

        :return: Revision number dictionary.
        :rtype: dict
        """
        with self._rev_lock:
            try:
                with open(self.rev_file, "rb") as f:
                    rev_dict = umsgpack.unpack(f)
            except FileNotFoundError:
                rev_dict = {}

            return rev_dict

    def get_local_rev(self, dbx_path):
        """
        Gets revision number of local file.

        :param str dbx_path: Dropbox file path.
        :return: Revision number as str or `None` if no local revision number
            has been saved.
        :rtype: str
        """
        with self._rev_lock:
            dbx_path = dbx_path.lower()

            try:
                with open(self.rev_file, "rb") as f:
                    rev_dict = umsgpack.unpack(f)
            except FileNotFoundError:
                rev_dict = {}

            try:
                rev = rev_dict[dbx_path]
            except KeyError:
                rev = None

            return rev

    def set_local_rev(self, dbx_path, rev):
        """
        Saves revision number `rev` for local file. If `rev` is `None`, the
        entry for the file is removed.

        :param str dbx_path: Relative Dropbox file path.
        :param rev: Revision number as string or `None`.
        """
        with self._rev_lock:
            dbx_path = dbx_path.lower()
            try:
                with open(self.rev_file, "rb") as f:
                    rev_dict = umsgpack.unpack(f)
            except FileNotFoundError:
                rev_dict = {}

            if rev is None:  # remove entries for dbx_path and its children
                for path in dict(rev_dict):
                    if path.startswith(dbx_path):
                        rev_dict.pop(path, None)
            else:
                rev_dict[dbx_path] = rev
                # set all parent revs to 'folder'
                dirname = osp.dirname(dbx_path)
                while dirname is not "/":
                    rev_dict[dirname] = "folder"
                    dirname = osp.dirname(dirname)

            with open(self.rev_file, "wb+") as f:
                umsgpack.pack(rev_dict, f)

    def get_account_info(self):
        """
        Gets current account information.

        :return: :class:`dropbox.users.FullAccount` instance or `None` if failed.
        :rtype: dropbox.users.FullAccount
        """
        try:
            res = self.dbx.users_get_current_account()
        except dropbox.exceptions.ApiError as err:
            logging.debug("Failed to get account info: %s", err)
            res = None

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
        try:
            res = self.dbx.users_get_space_usage()
        except dropbox.exceptions.ApiError as err:
            logging.debug("Failed to get space usage: %s", err)
            return False

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
        self.auth.unlink()

        os.remove(self.rev_file)

        self.excluded_folders = []
        CONF.set("main", "excluded_folders", [])

        CONF.set("account", "email", "")
        CONF.set("account", "usage", "")

        CONF.set("internal", "cursor", "")
        CONF.set("internal", "lastsync", None)

        logger.debug("Unlinked Dropbox account")

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
        except (dropbox.exceptions.ApiError, IOError, OSError) as exc:
            msg = ("An error occurred while downloading '{0}' file as '{1}': {2}.".format(
                    dbx_path, dst_path, exc.error if hasattr(exc, "error") else exc))
            logger.error(msg)
            return False

        msg = ("File '{0}' (rev {1}) from '{2}' was successfully downloaded as '{3}'.\n".format(
                md.name, md.rev, md.path_display, dst_path))
        logger.debug(msg)

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

        pb = tqdm(total=file_size, unit="B", unit_scale=True,
                  desc=osp.basename(local_path), miniters=1,
                  ncols=80, mininterval=1)
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
                        pb.update(chunk_size)
                        if file_size - f.tell() <= chunk_size:
                            pb.update(file_size - f.tell())
                            md = self.dbx.files_upload_session_finish(
                                f.read(chunk_size), cursor, commit)
                        else:
                            self.dbx.files_upload_session_append_v2(
                                f.read(chunk_size), cursor)
                            cursor.offset = f.tell()
        except dropbox.exceptions.ApiError as exc:
            msg = "An error occurred while uploading file '{0}': {1}.".format(
                local_path, exc.error.get_path().reason)
            logger.error(msg)
            return False
        finally:
            pb.close()

        logger.debug("File '%s' (rev %s) uploaded to Dropbox.", md.path_display, md.rev)
        return md

    def remove(self, dbx_path, **kwargs):
        """
        Removes file / folder from Dropbox.

        :param str dbx_path: Path to file on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_delete.
        :return: Metadata of deleted file or `False` if deletion failed.
        """
        try:
            # try to move file (response will be metadata, probably)
            md = self.dbx.files_delete(dbx_path, **kwargs)
        except dropbox.exceptions.ApiError as err:
            logger.debug("An error occurred when deleting '%s': %s", dbx_path, err)
            return False

        logger.debug("File / folder '%s' removed from Dropbox.", dbx_path)

        return md

    def move(self, dbx_path, new_path):
        """
        Moves/renames files or folders on Dropbox.

        :param str dbx_path: Path to file/folder on Dropbox.
        :param str new_path: New path on Dropbox to move to.
        :return: Metadata of moved file/folder or `False` if move failed.
        """
        try:
            # try to move file
            metadata = self.dbx.files_move(dbx_path, new_path,
                                           allow_shared_folder=True,
                                           allow_ownership_transfer=True)
        except dropbox.exceptions.ApiError as err:
            logger.debug(
                    "An error occurred when moving '%s' to '%s': %s",
                    dbx_path, new_path, err)
            return False

        logger.debug("File moved from '%s' to '%s' on Dropbox.",
                     dbx_path, metadata.path_display)

        return metadata

    def make_dir(self, dbx_path, **kwargs):
        """
        Creates folder on Dropbox.

        :param str dbx_path: Path o fDropbox folder.
        :param kwargs: Keyword arguments for Dropbox SDK files_create_folder.
        :return: Metadata of created folder or `False` if failed.
        """
        try:
            md = self.dbx.files_create_folder(dbx_path, **kwargs)
        except dropbox.exceptions.ApiError as err:
            logger.debug("An error occurred creating dir '%s': %s", dbx_path, err)
            return False

        logger.debug("Created folder '%s' on Dropbox.", md.path_display)

        return md

    def list_folder(self, dbx_path, **kwargs):
        """
        Lists contents of a folder on Dropbox as dictionary mapping unicode
        file names to FileMetadata|FolderMetadata entries.

        :param str dbx_path: Path of folder on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_list_folder.
        :return: A list of :class:`dropbox.files.ListFolderResult` instances or
            `False` if failed.
        :rtype: list
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
                logger.debug("Folder listing failed for '{0}': {1}".format(dbx_path, err))
                return False

        logger.debug("Listed contents of folder '{0}'".format(dbx_path))

        return results

    def flatten_results_list(self, results):
        """
        Flattens a list of :class:`dropbox.files.ListFolderResult` instances
        and returns their entries only. Any cursors will be lost.

        :param list results: List of :class:`dropbox.files.ListFolderResult`
            instances.
        :return: List of Dropbox API file/folder/deleted metadata.
        :rtype: list

        """
        results_list = []
        for res in results:
            for entry in res.entries:
                results_list.append(entry)

        return results_list

    def get_remote_dropbox(self, dbx_path=""):
        """
        Gets all files/folders from Dropbox and writes them to local folder
        :ivar:`dropbox_path`. Call this method on first run of client. Indexing
        and downloading may take some time, depending on the size of the users
        Dropbox folder.

        :param str dbx_path: Path to Dropbox folder. Defaults to root ("").
        :return: `True` on success, `False` otherwise.
        :rtype: bool
        """
        results = self.list_folder(dbx_path, recursive=True,
                                   include_deleted=False, limit=500)

        if not results:
            return False

        # apply remote changes, don't update the global cursor when downloading
        # a single folder only
        save_cursor = (dbx_path == "")
        success = self.apply_remote_changes(results, save_cursor)

        return success

    def wait_for_remote_changes(self, timeout=20):
        """
        Waits for remote changes since :ivar:`last_cursor`. Call this method
        after starting the Dropbox client and periodically to get the latest
        updates.

        :param int timeout: Seconds to wait until timeout.
        :return: `True` if changes are available, `False` otherwise.
        :rtype: bool
        """

        logger.debug("Waiting for remote changes since cursor:\n{0}".format(self.last_cursor))

        # honour last request to back off
        if self.last_longpoll is not None:
            while time.time() - self.last_longpoll < self.backoff:
                time.sleep(1)

        try:
            result = self.dbx.files_list_folder_longpoll(self.last_cursor, timeout=timeout)
        except dropbox.exceptions.ApiError:
            msg = "Cannot access Dropbox folder."
            logger.debug(msg)
            return False

        # keep track of last long poll, back off if requested by SDK
        if result.backoff:
            self.backoff = result.backoff + 5
        else:
            self.backoff = 0

        self.last_longpoll = time.time()

        return result.changes

    def list_remote_changes(self):
        """
        Lists changes to remote Dropbox since :ivar:`last_cursor`. Call this
        after :method:`wait_for_remote_changes` returns `True`.

        :return: List of :class:`dropbox.files.ListFolderResult`
            instances or empty list if requests failed.
        :rtype: list
        """

        results = [0]

        try:
            results[0] = self.dbx.files_list_folder_continue(self.last_cursor)
        except dropbox.exceptions.ApiError as err:
            logging.info("Folder listing failed: %s", err)
            return []

        while results[-1].has_more:
            try:
                result = self.dbx.files_list_folder_continue(results[-1].cursor)
                results.append(result)
            except dropbox.exceptions.ApiError as err:
                logging.info("Folder listing failed: %s", err)
                return []

        # count remote changes
        total = 0
        for result in results:
            total += len(result.entries)

        # notify user
        if total == 1:
            md = results[0].entries[0]
            if isinstance(md, DeletedMetadata):
                self.notify.send("%s removed" % md.path_display)
            else:
                self.notify.send("%s added" % md.path_display)
        elif total > 1:
            self.notify.send("%s files changed" % total)

        logger.debug("Listed remote changes")

        return results

    def apply_remote_changes(self, results, save_cursor=True):
        """
        Applies remote changes to local folder. Call this on the result of
        :method:`list_remote_changes`. The saved cursor is updated after a set
        of changes has been successfully applied.

        :param list results: List of :class:`dropbox.files.ListFolderResult`
            instances or empty list if requests failed.
        :return: List of :class:`dropbox.files.ListFolderResult`
            instances or empty list if requests failed.
        :param bool save_cursor: If True, :ivar:`last_cursor` will be updated
            from the last applied changes.
        :return: `True` on success, `False` otherwise.
        :rtype: bool
        """
        all_folders = []
        all_files = []
        all_deleted = []

        # apply remote changes
        for result in results:

            # sort changes into folders, files and deleted
            folders, files, deleted = self._sort_entries(result)

            all_folders += folders
            all_files += files
            all_deleted += deleted

        # sort according to path hierarchy
        # do not create sub-folder / file before parent exists
        all_folders.sort(key=lambda x: len(x.path_display.split('/')))
        all_files.sort(key=lambda x: len(x.path_display.split('/')))
        all_deleted.sort(key=lambda x: len(x.path_display.split('/')))

        # all_folders_binned = []
        # for depth in itertools.count(start=2, step=1):
        #     depth_folders = []
        #     for folder in all_folders:
        #         if len(folder.path_display.split('/')) == depth:
        #             depth_folders.append(folder)
        #     if depth_folders:
        #         all_folders_binned.append(depth_folders)
        #     else:
        #         break

        # create local folders, start with top-level and work your way down
        for folder in all_folders:
            success = self._create_local_entry(folder)
            if success is False:
                return False

        # apply created files
        with ThreadPoolExecutor(max_workers=15) as executor:
            success = executor.map(self._create_local_entry, all_files)
        if all(success) is False:
            return False

        # apply deleted items
        with ThreadPoolExecutor(max_workers=15) as executor:
            success = executor.map(self._create_local_entry, all_deleted)
        if all(success) is False:
            return False

        # save cursor
        if save_cursor:
            self.last_cursor = results[-1].cursor
            CONF.set("internal", "cursor", result.cursor)

        return True

    @staticmethod
    def _sort_entries(result):
        """
        Sorts entries in :class:`dropbox.files.ListFolderResult` into
        FolderMetadata, FileMetadata and DeletedMetadata.

        :return: Tuple of (folders, files, deleted) containing instances of
            :class:`DeletedMetadata`, `:class:FolderMetadata`,
            and :class:`FileMetadata` respectively.
        :rtype: tuple
        """

        folders = [x for x in result.entries if isinstance(x, FolderMetadata)]
        files = [x for x in result.entries if isinstance(x, FileMetadata)]
        deleted = [x for x in result.entries if isinstance(x, DeletedMetadata)]

        return folders, files, deleted

    def _create_local_entry(self, entry, check_excluded=True):
        """
        Creates local file / folder for remote entry.

        :param class entry: Dropbox FileMetadata|FolderMetadata|DeletedMetadata.
        :return: `True` on success, `False` otherwise.
        :rtype: bool
        """

        self.excluded_folders = CONF.get("main", "excluded_folders")

        if self.is_excluded(entry.path_display):
            return True

        if check_excluded and self.is_excluded_by_user(entry.path_display):
            return True

        if isinstance(entry, FileMetadata):
            # Store the new entry at the given path in your local state.
            # If the required parent folders don’t exist yet, create them.
            # If there’s already something else at the given path,
            # replace it and remove all its children.

            dst_path = self.to_local_path(entry.path_display)

            # check for sync conflicts
            conflict = self.check_conflict(entry.path_display)
            if conflict == -1:  # could not get metadata
                return False
            if conflict == 0:  # no conflict
                pass
            elif conflict == 1:  # conflict! rename local file
                parts = osp.splitext(dst_path)
                new_local_file = parts[0] + " (conflicting copy)" + parts[1]
                os.rename(dst_path, new_local_file)
            elif conflict == 2:  # Dropbox files corresponds to local file, nothing to do
                return True

            md = self.download(entry.path_display, dst_path)
            if md is False:
                return False

            # save revision metadata
            self.set_local_rev(md.path_display, md.rev)

            return True

        elif isinstance(entry, FolderMetadata):
            # Store the new entry at the given path in your local state.
            # If the required parent folders don’t exist yet, create them.
            # If there’s already something else at the given path,
            # replace it but leave the children as they are.

            dst_path = self.to_local_path(entry.path_display)
            os.makedirs(dst_path, exist_ok=True)

            self.set_local_rev(entry.path_display, "folder")

            logger.debug("Created local directory '{0}'".format(entry.path_display))

            return True

        elif isinstance(entry, DeletedMetadata):
            # If your local state has something at the given path,
            # remove it and all its children. If there’s nothing at the
            # given path, ignore this entry.

            dst_path = self.to_local_path(entry.path_display)

            if osp.isdir(dst_path):
                shutil.rmtree(dst_path)
            elif osp.isfile(dst_path):
                os.remove(dst_path)

            self.set_local_rev(entry.path_display, None)

            logger.debug("Deleted local item '{0}'".format(entry.path_display))

            return True

    def is_excluded_by_user(self, dbx_path):
        """
        Check if file is excluded from sync.

        :param str dbx_path: Path of folder on Dropbox.
        :return: `True` or `False`.
        :rtype: bool
        """
        dbx_path = dbx_path.lower()

        excluded = False

        # in excluded folders?
        for excluded_folder in self.excluded_folders:
            if not osp.commonpath([dbx_path, excluded_folder]) in ["/", ""]:
                excluded = True

        return excluded

    def is_excluded(self, dbx_path):
        """
        Check if file is excluded from sync.

        :param str dbx_path: Path of folder on Dropbox.
        :return: `True` or `False`.
        :rtype: bool
        """
        dbx_path = dbx_path.lower()

        excluded = False

        # is root folder?
        if dbx_path in ["/", ""]:
            excluded = True

        # in excluded files?
        if osp.basename(dbx_path) in self.excluded_files:
            excluded = True

        # If the file name contains multiple periods it is likely a temporary
        # file created during a saving event on macOS. Ignore such files.
        if osp.basename(dbx_path).count(".") > 1:
            excluded = True

        return excluded

    def check_conflict(self, dbx_path):
        """
        Check if local file is conflicting with remote file.

        :param str dbx_path: Path of folder on Dropbox.
        :return: 0 for no conflict, 1 for conflict, 2 if files are identical.
            Returns -1 if metadata request to Dropbox API fails.
        :rtype: int
        """
        # get corresponding local path
        dst_path = self.to_local_path(dbx_path)

        # get metadata of remote file
        try:
            md = self.dbx.files_get_metadata(dbx_path)
        except dropbox.exceptions.ApiError as err:
            logging.info("Could not get metadata for '%s': %s", dbx_path, err)
            return -1

        # no conflict if local file does not exist yet
        if not osp.exists(dst_path):
            logger.debug("Local file '%s' does not exist. No conflict.", dbx_path)
            return 0

        local_rev = self.get_local_rev(dbx_path)

        # check if remote and local versions have same rev
        if not md.rev == local_rev:
            # Dropbox server version has a different rev, must be newer
            logger.debug(
                    "Local file has rev %s, file on Dropbox has rev %s. Get Dropbox file.",
                    local_rev, md.rev)
            return 0

        if local_rev is None:
            # We likely have a conflict: files with the same name have been
            # created on Dropbox and locally independent of each other.
            # If a file has been modified while the client was not running,
            # its entry from rev_dict is removed. Check actual content first before
            # declaring conflict!

            local_hash = get_local_hash(dst_path)

            if not md.content_hash == local_hash:
                logger.debug("Conflicting copy without rev.")
                return 1  # files are conflicting
            else:
                logger.debug("Contents are equal. No conflict. Updated local rev.")
                self.set_local_rev(dbx_path, md.rev)
                return 2  # files are already the same

        if md.rev == local_rev:
            # files have the same revision, trust that they are equal
            logger.debug(
                    "Local file is the same as on Dropbox (rev %s). No download necessary.",
                    local_rev)
            return 2  # files are already the same
