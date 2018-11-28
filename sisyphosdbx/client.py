import os
import os.path as osp
import time
import datetime
import logging
import pickle
from tqdm import tqdm
import shutil
import dropbox
from dropbox import DropboxOAuth2FlowNoRedirect
from dropbox import files

from sisyphosdbx.config.main import CONF, SUBFOLDER
from sisyphosdbx.config.base import get_conf_path
from sisyphosdbx.notify.main import Notipy


logger = logging.getLogger(__name__)


def megabytes_to_bytes(size_mb):
    """
    Convert size in bytes to megabytes
    """
    return size_mb * 1024 * 1024


class OAuth2Session(object):
    """Provides OAuth2 login and token store.

    :ivar app_key: String containing app key provided by Dropbox.
    :ivar app_secret: String containing app secret provided by Dropbox.
    """

    TOKEN_FILE = osp.join(get_conf_path(SUBFOLDER), "o2_store.txt")
    auth_flow = None
    oAuth2FlowResult = None
    access_token = ""
    account_id = ""
    user_id = ""

    def __init__(self, app_key="", app_secret=""):
        # prepare auth flow
        self.auth_flow = DropboxOAuth2FlowNoRedirect(app_key, app_secret)
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
            self.access_token, self.account_id, self.user_id = stored_creds.split('|')
            print(" [OK]")
        except IOError:
            print(" [FAILED]")
            print(" x Access token not found. Beginning new session.")
            self.link()

    def write_creds(self):
        with open(self.TOKEN_FILE, 'w+') as f:
            f.write("|".join([self.access_token, self.account_id, self.user_id]))

        print(" > Credentials written.")

    def delete_creds(self):
        os.unlink(self.TOKEN_FILE)
        print(" > Credentials removed.")

    def unlink(self):
        self.delete_creds()
        # I can't unlink the app yet properly (API limitation), so let's just remove the token


class SisyphosClient(object):
    """Client for Dropbox SDK.

    This client defines basic methods to edit the remote Dropbox folder: it
    supports creating, moving, modifying and deleting files and folders on
    Dropbox. It also provides a method to download a file from Dropbox to a
    given local path.

    Higher level methods provide ways to list the contents of and download
    entire folder from Dropbox.

    SisyphosClient also provides methods to wait for and apply changes from the
    remote Dropbox. Detecting local changes is handled by :class:`LocalMonitor`
    instead.

    All Dropbox API errors are caught and handled here. HTTP errors indicate
    a problem with the connection to Dropbox servers and are raised. They will
    be cought and handled by :class:`RemoteMonitor` instead.

    :ivar flagged: List keeping track of recently updated file/folder paths.
        Entries will be removed by :class:`LocalMonitor` once it delects the
        corresponding file change.
    :ivar last_cursor: Last cursor from Dropbox which was synced. The value
        is updated and saved to config file on every successful sync.
    :ivar exlcuded_files: List containing all files excluded from sync.
        This only contains system files such as '.DS_STore' and internal files
        such as '.dropbox' and should not be changed.
    :ivar excluded_folders: List containing all files excluded from sync.
        When adding and removing entries, make sure to update the config file
        as well so that changes persist accross sessions.
    :ivar rev_dict: Dictionary with paths to all synced local files/folders
        as keys. Values are the revision number of a file or 'folder' for a
        folder. Do not change entries manually, the dict is updated
        automatically with every sync. :ivar:`rev_dict` is used to determine
        sync conflicts and detect deleted files while SisyphosDBX has not been
        running. :ivar:`rev_dict` is periodically saved to :ivar:`rev_file`.
        All keys are stored in lower case.
    :ivar rev_file: Path of local file to save :ivar:`rev_dict`. This defaults
        to '/dropbox_path/.dropbox'
    :ivar dropbox_path: Path to local Dropbox folder, as loaded from config
        file. Before changing :ivar`dropbox_path`, make sure that all syncing
        is paused. Make sure to move the local Dropbox directory before
         resuming the sync and to save the new :ivar`dropbox_path` to the
         config file.
    """

    APP_KEY = '2jmbq42w7vof78h'
    APP_SECRET = 'lrsxo47dvuulex5'
    SDK_VERSION = "2.0"

    exlcuded_files = CONF.get('main', 'exlcuded_files')
    excluded_folders = CONF.get('main', 'excluded_folders')
    last_cursor = CONF.get('internal', 'cursor')
    flagged = []

    dropbox = None
    session = None

    notipy = Notipy()

    def __init__(self):
        # check if I specified app_key and app_secret
        if self.APP_KEY == '' or self.APP_SECRET == '':
            exit(' x You need to set your APP_KEY and APP_SECRET!')

        # get Dropbox session
        self.session = OAuth2Session(self.APP_KEY, self.APP_SECRET)
        self.last_longpoll = None
        self.backoff = 0

        # initialize API client
        self.dbx = dropbox.Dropbox(self.session.access_token)
        print(' > SisyphusClient is ready.')

        # get correct directories
        self.dropbox_path = CONF.get('main', 'path')
        self.rev_file = osp.join(self.dropbox_path, '.dropbox')
        # try to load revisions dictionary
        try:
            with open(self.rev_file, 'rb') as f:
                self.rev_dict = pickle.load(f)
        except FileNotFoundError:
            self.rev_dict = {}

    def to_dbx_path(self, local_path):
        """
        Converts a local path to a path relative to the Dropbox folder.

        :param str local_path: Full path to file in local Dropbox folder.
        :return: Relative path with respect to Dropbox folder.
        :rtype: str
        :raises ValueError: If no path is specfied or path is outide of local
            Dropbox folder.
        """

        if not local_path:
            raise ValueError("No path specified.")

        dbx_root_list = osp.normpath(self.dropbox_path).split(osp.sep)
        path_list = osp.normpath(local_path).split(osp.sep)

        # Work out how much of the filepath is shared by dropbox_path and path.
        i = len(osp.commonprefix([dbx_root_list, path_list]))

        if i == len(path_list):  # path corresponds to dropbox_path
            return "/"
        elif not i == len(dbx_root_list):  # path is outside of to dropbox_path
            raise ValueError("Specified path '%s' is not in Dropbox directory." % local_path)

        relative_path = '/' + '/'.join(path_list[i:])

        return relative_path

    def to_local_path(self, dbx_path):
        """
        Converts a Dropbox folder path the correspoding local path.

        :param str dbx_path: Path to file relative to Dropbox folder.
        :return: Corresponding local path on drive.
        :rtype: str
        :raises ValueError: If no path is specfied.
        """

        if not dbx_path:
            raise ValueError("No path specified.")

        path = dbx_path.replace('/', osp.sep)
        path = osp.normpath(path)

        return osp.join(self.dropbox_path, path.lstrip(osp.sep))

    def get_local_rev(self, dbx_path):
        """
        Gets revision number of local file, as saved in :ivar:`rev_dict`.

        :param str dbx_path: Dropbox file path.
        :return: Revision number as str or `None` if no local revision number
            has been saved.
        :rtype: str
        """
        dbx_path = dbx_path.lower()
        try:
            with open(self.rev_file, 'rb') as f:
                self.rev_dict = pickle.load(f)
        except FileNotFoundError:
            self.rev_dict = {}

        try:
            rev = self.rev_dict[dbx_path]
        except KeyError:
            rev = None

        return rev

    def set_local_rev(self, dbx_path, rev):
        """
        Saves revision number `rev` for local file. If `rev` is `None`, the
        entry for the file is removed.

        :param str dbx_path: Relative Dropbox file path.
        :param str rev: Revision number as string.
        """
        dbx_path = dbx_path.lower()

        if rev is None:  # remove entries for dbx_path and its children
            for path in dict(self.rev_dict):
                if path.startswith(dbx_path):
                    self.rev_dict.pop(path, None)
        else:
            self.rev_dict[dbx_path] = rev
            # set all parent revs to 'folder'
            dirname = osp.dirname(dbx_path)
            while dirname is not '/':
                self.rev_dict[dirname] = 'folder'
                dirname = osp.dirname(dirname)

        with open(self.rev_file, 'wb+') as f:
            pickle.dump(self.rev_dict, f, pickle.HIGHEST_PROTOCOL)

    def unlink(self):
        """
        Unlinks the Dropbox account.
        """
        self.dbx.unlink()

    def list_folder(self, folder, **kwargs):
        """
        Lists contents of a folder on Dropbox as dictionary mapping unicode
        filenames to FileMetadata|FolderMetadata entries.

        :param str path: Path of folder on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_list_folder.
        :return: A dict mapping unicode filenames to
            FileMetadata|FolderMetadata entries or `None` if failed.
        :rtype: dict
        """
        path = osp.normpath(folder)

        results = []

        try:
            results.append(self.dbx.files_list_folder(path, **kwargs))
        except dropbox.exceptions.ApiError as err:
            logging.info("Folder listing failed for '%s': %s", path, err)
            return None

        while results[-1].has_more:
            try:
                more_results = self.dbx.files_list_folder_continue(results[-1].cursor)
                results.append(more_results)
            except dropbox.exceptions.ApiError as err:
                logging.info("Folder listing failed for '%s': %s", path, err)
                return None

        rv = {}
        for res in results:
            for entry in res.entries:
                rv[entry.name] = entry

        return rv

    def download(self, dbx_path, **kwargs):
        """
        Downloads file from Dropbox to our local folder. Checks for sync
        conflicts. Downloads file or folder to the local Dropbox folder.

        :param str path: Path to file on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_download_to_file.
        :return: :class:`dropbox.files.FileMetadata` or
            :class:`dropbox.files.FolderMetadata` of downloaded file/folder,
            `False` if request fails or `None` if local copy is already in
            sync.
        """
        # generate local path from dropbox_path and given path parameter
        dst_path = self.to_local_path(dbx_path)
        dst_path_directory = osp.dirname(dst_path)

        if not osp.exists(dst_path_directory):
            os.makedirs(dst_path_directory)

        try:
            conflict = self._is_local_conflict(dbx_path)
        except dropbox.exceptions.ApiError as exc:
            msg = ("An error occurred while getting metadata of file '{0}': "
                   "{1}.".format(dbx_path, exc.error if hasattr(exc, 'error') else exc))
            logger.error(msg)
            return False

        if conflict == 0:  # no conflict
            pass
        elif conflict == 1:  # conflict! rename local file
            parts = osp.splitext(dst_path)
            new_local_file = parts[0] + ' (Dropbox conflicting copy)' + parts[1]
            os.rename(dst_path, new_local_file)
        elif conflict == 2:  # Dropbox files corresponds to local file, nothing to do
            return None

        try:
            md = self.dbx.files_download_to_file(dst_path, dbx_path, **kwargs)
        except (dropbox.exceptions.ApiError, IOError, OSError) as exc:
            msg = ("An error occurred while downloading '{0}' file as '{1}': {2}.".format(
                    dbx_path, dst_path, exc.error if hasattr(exc, 'error') else exc))
            logger.error(msg)
            return False

        self.set_local_rev(md.path_display, md.rev)  # save revision metadata
        self.flagged.append(md.path_display)

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
        chunk_size = megabytes_to_bytes(chunk_size)

        pb = tqdm(total=file_size, unit="B", unit_scale=True,
                  desc=osp.basename(local_path), miniters=1,
                  ncols=80, mininterval=1)
        mtime = os.path.getmtime(local_path)
        mtime_dt = datetime.datetime(*time.gmtime(mtime)[:6])

        try:
            with open(local_path, 'rb') as f:
                if file_size <= chunk_size:
                    md = self.dbx.files_upload(
                            f.read(), dbx_path, client_modified=mtime_dt, **kwargs)
                else:
                    session_start = self.dbx.files_upload_session_start(
                        f.read(chunk_size))
                    cursor = files.UploadSessionCursor(
                        session_id=session_start.session_id, offset=f.tell())
                    commit = files.CommitInfo(
                            path=dbx_path, client_modified=mtime, **kwargs)
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

        self.set_local_rev(md.path_display, md.rev)  # save revision metadata
        logger.debug("File '%s' (rev %s) uploaded to Dropbox.", md.path_display, md.rev)
        return md

    def remove(self, dbx_path, **kwargs):
        """
        Removes file from Dropbox.

        :param str dbx_path: Path to file on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_delete.
        :return: Metadata of deleted file or `False` if deletion failed.
        """
        try:
            # try to move file (response will be metadata, probably)
            md = self.dbx.files_delete(dbx_path, **kwargs)
        except dropbox.exceptions.ApiError as err:
            logger.debug("An error occured when deleting '%s': %s", dbx_path, err)
            return False

        # remove revision metadata
        self.set_local_rev(md.path_display, None)

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
                    "An error occured when moving '%s' to '%s': %s",
                    dbx_path, new_path, err)
            return False

        # update local revs
        self.set_local_rev(dbx_path, None)

        if isinstance(metadata, files.FileMetadata):
            self.set_local_rev(new_path, metadata.rev)

        elif isinstance(metadata, files.FolderMetadata):  # set rev of children
            self.set_local_rev(new_path, 'folder')
            results = self.list_folder(new_path, recursive=True)
            for md in results.values():
                if isinstance(md, files.FileMetadata):
                    self.set_local_rev(md.path_display, md.rev)
                elif isinstance(md, files.FolderMetadata):
                    self.set_local_rev(md.path_display, 'folder')

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
            logger.debug("An error occured creating dir '%s': %s", dbx_path, err)
            return False

        self.set_local_rev(dbx_path, 'folder')

        logger.debug("Created folder '%s' on Dropbox.", md.path_display)

        return md

    def get_remote_dropbox(self, path=""):
        """
        Gets all files/folders from Dropbox and writes them to local folder
        :ivar:`dropbox_path`. Call this method on first run of client. Indexing
        and downloading may take some time, depending on the size of the users
        Dropbox folder.

        :param str path: Path to folder on Dropbox, defaults to root.
        :return: `True` on success, `False` otherwise.
        :rtype: bool
        """
        results = []  # list to store all results

        try:  # get metadata of all remote folders and files
            results.append(self.dbx.files_list_folder(path, recursive=True,
                                                      include_deleted=False,
                                                      limit=500))
        except dropbox.exceptions.ApiError as exc:
            msg = "Cannot access '{0}': {1}".format(path, exc.error.get_path())
            logger.debug(msg)
            return False

        idx = 0

        while results[-1].has_more:  # check if there is more
            idx += len(results[-1].entries)
            logger.info("Indexing %s" % idx)
            try:
                more_results = self.dbx.files_list_folder_continue(results[-1].cursor)
                results.append(more_results)
            except dropbox.exceptions.ApiError as exc:
                msg = "Cannot access '{0}': {1}".format(path, exc.error.get_path())
                logger.debug(msg)
                return False

        # count remote changes
        total = 0
        for result in results:
            total += len(result.entries)

        # apply remote changes
        idx = 0
        for result in results:
            for entry in result.entries:
                idx += 1
                logger.info("Downloading %s/%s" % (idx, total))
                self._create_local_entry(entry)

            if path == "":  # save cursor only if synced for whole dropbox
                self.last_cursor = result.cursor
                CONF.set('internal', 'cursor', result.cursor)
                CONF.set('internal', 'lastsync', time.time())

        return True

    def wait_for_remote_changes(self, timeout=120):
        """
        Waits for remote changes since :ivar:`last_cursor`. Call this method
        after starting the Dropbox client and periodically to get the latest
        updates.

        :param int timeout: Seconds to wait until timeout.
        """
        # honour last request to back off
        if self.last_longpoll is not None:
            while time.time() - self.last_longpoll < self.backoff:
                time.sleep(1)

        try:  # get metadata of all remote folders and files
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

    def get_remote_changes(self):
        """
        Downloads / applies remote changes to local folder since
        :ivar:`last_cursor`.

        :param int timeout: Seconds to wait untill timeout.
        :return: `True` on success, `False` otherwise.
        :rtype: bool
        """

        results = [0]

        try:
            results[0] = self.dbx.files_list_folder_continue(self.last_cursor)
        except dropbox.exceptions.ApiError as err:
            logging.info("Folder listing failed: %s", err)
            return False

        while results[-1].has_more:
            try:
                result = self.dbx.files_list_folder_continue(results[-1].cursor)
                results.append(result)
            except dropbox.exceptions.ApiError as err:
                logging.info("Folder listing failed: %s", err)
                return False

        # count remote changes
        total = 0
        for result in results:
            total += len(result.entries)

        # notify user
        if total == 1:
            md = results[0].entries[0]
            if isinstance(md, files.DeletedMetadata):
                self.notipy.send("%s removed" % md.path_display)
            else:
                self.notipy.send("%s added" % md.path_display)
        elif total > 1:
            self.notipy.send("%s files changed" % total)

        # apply remote changes
        for result in results:
            for entry in result.entries:
                self._create_local_entry(entry)

            self.last_cursor = result.cursor
            CONF.set('internal', 'cursor', result.cursor)
            CONF.set('internal', 'lastsync', time.time())

        return True

    def _create_local_entry(self, entry):
        """Creates local file / folder for remote entry.

        :param class entry: Dropbox FileMetadata|FolderMetadata|DeletedMetadata.
        """

        self.excluded_folders = CONF.get('main', 'excluded_folders')

        if self.is_excluded(entry.path_display):
            return

        if isinstance(entry, files.FileMetadata):
            # Store the new entry at the given path in your local state.
            # If the required parent folders don’t exist yet, create them.
            # If there’s already something else at the given path,
            # replace it and remove all its children.

            self.download(entry.path_display)

        elif isinstance(entry, files.FolderMetadata):
            # Store the new entry at the given path in your local state.
            # If the required parent folders don’t exist yet, create them.
            # If there’s already something else at the given path,
            # replace it but leave the children as they are.

            dst_path = self.to_local_path(entry.path_display)

            if not osp.isdir(dst_path):
                self.flagged.append(entry.path_display)
                os.makedirs(dst_path)

            self.set_local_rev(entry.path_display, 'folder')

        elif isinstance(entry, files.DeletedMetadata):
            # If your local state has something at the given path,
            # remove it and all its children. If there’s nothing at the
            # given path, ignore this entry.

            dst_path = self.to_local_path(entry.path_display)

            if osp.isdir(dst_path):
                self.flagged.append(entry.path_display)
                shutil.rmtree(dst_path)
            elif osp.isfile(dst_path):
                self.flagged.append(entry.path_display)
                os.remove(dst_path)

            self.set_local_rev(entry.path_display, None)

    def is_excluded(self, dbx_path):
        """
        Check if file is excluded from sync.

        :param str dbx_path: Path of folder on Dropbox.
        :return: `True` or `False`.
        :rtype: bool
        """
        dbx_path = dbx_path.lower()

        excluded = False
        # in excluded files?
        if os.path.basename(dbx_path) in self.exlcuded_files:
            excluded = True

        # in excluded folders?
        for excluded_folder in self.excluded_folders:
            if not os.path.commonpath([dbx_path, excluded_folder]) in ["/", ""]:
                excluded = True

        # is root folder?
        if dbx_path in ["/", ""]:
            excluded = True

        # If the file name contains multiple periods it is likely a temporary
        # file created during a saving event on macOS. Irgnore such files.
        if osp.basename(dbx_path).count('.') > 1:
            excluded = True

        return excluded

    def _is_local_conflict(self, dbx_path):
        """
        Check if local copy is conflicting with remote.

        :param str dbx_path: Path of folder on Dropbox.
        :return: 0 for conflict, 1 for no conflict, 2 if files are identical.
            Returns -1 if metadata request to Dropbox API fails.
        :rtype: int
        """
        # get corresponding local path
        dst_path = self.to_local_path(dbx_path)

        # no conflict if local file does not exist yet
        if not osp.exists(dst_path):
            logger.debug("Local file '%s' does not exist. No conflict.", dbx_path)
            return 0

        # get metadata otherwise
        try:
            md = self.dbx.files_get_metadata(dbx_path)
        except dropbox.exceptions.ApiError as err:
            logging.info("Could not get metadata for '%s': %s", dbx_path, err)
            return -1

        # check if Dropbox rev is in local dict
        local_rev = self.get_local_rev(dbx_path)
        if local_rev is None:
            # We have a conflict: files with the same name have been
            # created on Dropbox and locally inpedent from each other.
            # If a file has been modified while the client was not running,
            # its entry from rev_dict is removed.
            logger.debug("Conflicting local file without rev.")
            return 1
        # check if remote and local versions have same rev
        elif md.rev == local_rev:
            logger.debug(
                    "Local file is the same as on Dropbox (rev %s). No download necessary.",
                    local_rev)
            return 2  # files are already the same

        elif not md.rev == local_rev:
            # we are dealing with different revisions, trust the Dropbox server version
            logger.debug(
                    "Local file has rev %s, file on Dropbox has rev %s. Getting file from Dropbox.",
                    local_rev, md.rev)
            return 0
