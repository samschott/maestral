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
from watchdog.utils.dirsnapshot import DirectorySnapshot
from watchdog.events import (DirModifiedEvent, FileModifiedEvent,
                             DirCreatedEvent, FileCreatedEvent,
                             DirDeletedEvent, FileDeletedEvent)

from config.main import CONF, SUBFOLDER
from config.base import get_conf_path


logger = logging.getLogger(__name__)


def megabytes_to_bytes(size_mb):
    """
    Convert size in bytes to megabytes
    """
    return size_mb * 1024 * 1024


class OAuth2Session:
    """
    Provides OAuth2 login and token store.
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


class SisyphosClient:
    APP_KEY = '2jmbq42w7vof78h'
    APP_SECRET = 'lrsxo47dvuulex5'
    SDK_VERSION = "2.0"

    exlcuded_files = CONF.get('main', 'exlcuded_files')
    excluded_folders = CONF.get('main', 'excluded_folders')
    last_cursor = CONF.get('internal', 'cursor')

    dropbox = None
    session = None

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
        logger.info(' > SisyphusClient is ready.')

        # get correct directories
        self.dropbox_path = CONF.get('main', 'path')
        self.rev_file = osp.join(self.dropbox_path, '.dropbox')
        # try to load revisions dictionary
        try:
            with open(self.rev_file, 'rb') as f:
                self._rev_dict = pickle.load(f)
        except FileNotFoundError:
            self._rev_dict = {}

    def to_dbx_path(self, local_path):
        """Returns a relative version of a path, relative to Dropbox folder."""

        if not local_path:
            raise ValueError("No path specified.")

        start_list = osp.normpath(self.dropbox_path).split(osp.sep)
        path_list = osp.normpath(local_path).split(osp.sep)

        # Work out how much of the filepath is shared by start and path.
        i = len(osp.commonprefix([start_list, path_list]))

        rel_list = [osp.pardir] * (len(start_list)-i) + path_list[i:]
        if not rel_list:
            raise ValueError("Specified 'path' is not in Dropbox directory.")

        return '/' + '/'.join(rel_list)

    def to_local_path(self, dbx_path):
        """Converts a Dropbox folder path the correspoding local path."""

        path = dbx_path.replace('/', osp.sep)
        path = osp.normpath(path)

        return osp.join(self.dropbox_path, path.lstrip(osp.sep))

    def get_local_rev(self, dbx_path):
        """Gets local rev

        Gets revision number for local file.

        :param dbx_path: Dropbox file path
        :returns: revision str or None if no local revision number saved
        """
        try:
            with open(self.rev_file, 'rb') as f:
                self._rev_dict = pickle.load(f)
        except FileNotFoundError:
            self._rev_dict = {}

        try:
            rev = self._rev_dict[dbx_path]
        except KeyError:
            rev = None

        return rev

    def set_local_rev(self, dbx_path, rev):
        """Sets local rev

        Saves revision number for local file. If rev == None, the entry for the
        file is removed.

        :param dbx_path: Dropbox file path
        :param rev: revision str
        """
        if rev is None:
            self._rev_dict.pop(dbx_path, None)
        else:
            self._rev_dict[dbx_path] = rev

        with open(self.rev_file, 'wb+') as f:
            pickle.dump(self._rev_dict, f, pickle.HIGHEST_PROTOCOL)

    def unlink(self):
        """
        Kills current Dropbox session. Returns nothing.
        """
        self.dbx.unlink()

    def list_folder(self, folder, **kwargs):
        """List a folder.

        Return a dict mapping unicode filenames to
        FileMetadata|FolderMetadata entries.
        :param path: Path of folder on Dropbox.
        :param kwargs: keyword arguments for Dropbox SDK files_list_folder
        :returns: a dict mapping unicode filenames to
        FileMetadata|FolderMetadata entries.
        """
        path = osp.normpath(folder)

        try:
            res = self.dbx.files_list_folder(path, **kwargs)
        except dropbox.exceptions.ApiError as err:
            print('Folder listing failed for', path, '-- assumed empty:', err)
            return {}
        else:
            rv = {}
            for entry in res.entries:
                rv[entry.name] = entry
            return rv

    def download(self, dbx_path, **kwargs):
        """ Downloads file from Dropbox to our local folder.

        Checks for sync conflicts. Downloads file or folder to local Dropbox.

        :param path: path to file on Dropbox
        :param kwargs: keyword arguments for Dropbox SDK files_download_to_file
        :returns: metadata or False or None
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
                   "{2}.".format(dbx_path, exc.error if hasattr(exc, 'error') else exc))
            logger.warning(msg)
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
            msg = ("An error occurred while downloading '{0}' file as '{1}': "
                   "{2}.".format(
                           dbx_path, dst_path,
                           exc.error if hasattr(exc, 'error') else exc))
            logger.warning(msg)
            return False

        msg = ("File '{0}' (rev={1}) from '{2}' was successfully downloaded as '{3}'.\n".format(
                md.name, md.rev, md.path_display, dst_path))

        self.set_local_rev(md.path_display, md.rev)  # save revision metadata
        logger.info(msg)

        return md

    def upload(self, file_src, path, chunk_size=2, **kwargs):
        """
        Uploads local file to Dropbox.
        :param file: file to upload, bytes
        :param path: path to file on Dropbox
        :param kwargs: keyword arguments for Dropbox SDK files_upload
        :param chunk_size: Maximum size for individual uploads in MB. If the
            file size exceeds the chunk_size, an upload-session is created instead.
        :returns: metadata or False
        """

        file_size = osp.getsize(file_src)
        chunk_size = megabytes_to_bytes(chunk_size)

        pb = tqdm(total=file_size, unit="B", unit_scale=True,
                  desc=osp.basename(file_src), miniters=1,
                  ncols=80, mininterval=1)
        mtime = os.path.getmtime(file_src)
        mtime_dt = datetime.datetime(*time.gmtime(mtime)[:6])

        try:
            with open(file_src, 'rb') as f:
                if file_size <= chunk_size:
                    md = self.dbx.files_upload(
                            f.read(), path, client_modified=mtime_dt, **kwargs)
                else:
                    session_start = self.dbx.files_upload_session_start(
                        f.read(chunk_size))
                    cursor = files.UploadSessionCursor(
                        session_id=session_start.session_id, offset=f.tell())
                    commit = files.CommitInfo(
                            path=path, client_modified=mtime, **kwargs)
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
            msg = "An error occurred while uploading '{0}': {1}.".format(
                file_src, exc.error.get_path().reason)
            logger.warning(msg)
            return False
        finally:
            pb.close()

        self.set_local_rev(md.path_display, md.rev)  # save revision metadata
        logger.info("File uploaded properly.")
        return md

    def remove(self, path, **kwargs):
        """
        Removes file from Dropbox.
        :param path: path to file on Dropbox
        :param kwargs: keyword arguments for Dropbox SDK files_delete
        :returns: metadata or False
        """
        try:
            # try to move file (response will be metadata, probably)
            md = self.dbx.files_delete(path, **kwargs)
        except dropbox.exceptions.HttpError as err:
            logger.warning(' x HTTP error', err)
            return False
        except dropbox.exceptions.ApiError as err:
            logger.warning(' x API error', err)
            return False

        # remove revision metadata
        self.set_local_rev(md.path_display, None)

        return md

    def move(self, path, new_path):
        """
        Moves/renames files or folders on Dropbox
        :param path: path to file /folder on Dropbox
        :param new_path: new name/path
        :returns: metadata or False
        """
        try:
            # try to move file (response will be metadata, probably)
            md = self.dbx.files_move(path, new_path, allow_shared_folder=True,
                                     autorename=True, allow_ownership_transfer=True)
        except dropbox.exceptions.HttpError as err:
            logger.warning(' x HTTP error', err)
            return False
        except dropbox.exceptions.ApiError as err:
            logger.warning(' x API error', err)
            return False

        # update local revs
        self.set_local_rev(path, None)
        self.set_local_rev(new_path, md.rev)
        return md

    def make_dir(self, path, **kwargs):
        """
        Creates folder on Dropbox
        :param path: path to file /folder on Dropbox
        :param kwargs: keyword arguments for Dropbox SDK files_create_folder
        :returns: metadata or False
        """
        try:
            md = self.dbx.files_create_folder(path, **kwargs)
        except dropbox.exceptions.ApiError as err:
            logger.warning(' x API error', err)
            return False

        self.set_local_rev(path, 'folder')
        return md

    def get_remote_dropbox(self, path=""):
        """
        Gets all files/folders from dropbox and writes them to local folder.
        Call this method on first run of client. Indexing and downloading may
        take some time, depdning on the size of the users Dropbox folder.

        :param path: path to folder on Dropbox, defaults to root
        :returns: True on success, False otherwise
        """
        results = [0]  # list to store all results

        try:  # get metadata of all remote folders and files
            results[0] = self.dbx.files_list_folder(path, recursive=True,
                                                    include_deleted=False)
        except dropbox.exceptions.ApiError as exc:
            msg = "Cannot access '{0}': {1}".format(path, exc.error.get_path())
            logger.warning(msg)
            return False

        while results[-1].has_more:  # check if there is any more
            logger.info("Indexing %s" % len(results[-1].entries))
            more_results = self.dbx.files_list_folder_continue(results[-1].cursor)
            results.append(more_results)

        for result in results:
            for entry in result.entries:
                self._create_local_entry(entry)

            self.last_cursor = result.cursor
            CONF.set('internal', 'cursor', self.last_cursor)

        CONF.set('internal', 'lastsync', time.time())

        return True

    def get_local_changes(self):
        """Gets all local changes while app has not been running.

        Call this method on startup of client to upload all local changes.

        :returns: dictionary with all changes, keys are file paths relative to
            local Dropbox folder, entries are file changed event types
            corresponding to watchdog.
        """
        changes = []
        snapshot = DirectorySnapshot(self.dropbox_path)

        # get paths of modified or added files / folders
        for path in snapshot.paths:
            dbx_path = self.to_dbxd_path(path)
            if snapshot.mtime(path) > CONF.get('internal', 'lastsync'):
                if path in self._rev_dict:  # file is already tracked
                    if osp.isdir(path):
                        event = DirModifiedEvent(path)
                    else:
                        event = FileModifiedEvent(path)
                    changes.append(event)
                elif not self._is_excluded(dbx_path):
                    if osp.isdir(path):
                        event = DirCreatedEvent(path)
                    else:
                        event = FileCreatedEvent(path)
                    changes.append(event)

        # get deleted files / folders
        for path in self._rev_dict.keys():
            if path not in snapshot.paths:
                if path.endswith('/'):
                    event = DirDeletedEvent(path)
                else:
                    event = FileDeletedEvent(path)
                changes.append(event)

        return changes

    def wait_for_remote_changes(self, timeout=120):
        """Waits for remote changes since self.last_cursor.

        Waits for remote changes since self.last_cursor. Call this method after
        starting the Dropbox client and periodically to get the latest updates.

        :param timeout: seconds to wait untill timeout
        """
        # honour last request to back off
        if self.last_longpoll is not None:
            while time.time() - self.last_longpoll < self.backoff:
                time.sleep(1)

        try:  # get metadata of all remote folders and files
            result = self.dbx.files_list_folder_longpoll(self.last_cursor, timeout=timeout)

        except dropbox.exceptions.ApiError:
            msg = "Cannot access Dropbox folder."
            logger.warning(msg)
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
        Applies remote changes since self.last_cursor.
        :param timeout: seconds to wait untill timeout
        :returns: True on success, False otherwise
        """

        results = [0]

        results[0] = self.dbx.files_list_folder_continue(self.last_cursor)

        while results[-1].has_more:
            result = self.dbx.files_list_folder_continue(results[-1].cursor)
            results.append(result)

        for result in results:
            for entry in result.entries:
                self._create_local_entry(entry)

            self.last_cursor = result.cursor
            CONF.set('internal', 'cursor', self.last_cursor)
            CONF.set('internal', 'lastsync', time.time())

        return True

    def _create_local_entry(self, entry):
        """Creates local file / folder for remote entry
        :param entry:
        """

        self.excluded_folders = CONF.get('main', 'excluded_folders')

        if self._is_excluded(entry.path_display):
            return

        elif isinstance(entry, files.FileMetadata):
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
                os.makedirs(dst_path)

            self.set_local_rev(entry.path_display, 'folder')

        elif isinstance(entry, files.DeletedMetadata):
            # If your local state has something at the given path,
            # remove it and all its children. If there’s nothing at the
            # given path, ignore this entry.

            dst_path = self.to_local_path(entry.path_display)

            if osp.isdir(dst_path):
                shutil.rmtree(dst_path)
            elif osp.isfile(dst_path):
                os.remove(dst_path)

            self.set_local_rev(entry.path_display, None)

    def _is_excluded(self, path):
        """Check if file is excluded from sync
        :param path: Path of folder on Dropbox.
        :returns: True or False (bool)
        """
        excluded = False
        if os.path.basename(path) in self.exlcuded_files:
            excluded = True

        for excluded_folder in self.excluded_folders:
            if not os.path.commonpath([path, excluded_folder]) in ["/", ""]:
                excluded = True

        return excluded

    def _is_local_conflict(self, dbx_path):
        """Check if local copy is conflicting with remote.

        :param dbx_path: Path of folder on Dropbox.
        :returns: 0 for conflict, 1 for no conflict, 2 for files are identical
        """
        # get corresponding local path
        dst_path = self.to_local_path(dbx_path)

        # no conflict if local file does not exist yet
        if not osp.exists(dst_path):
            logger.info("Local file '%s' does not exist. No conflict.", dbx_path)
            return 0

        # get metadata otherwise
        md = self.dbx.files_get_metadata(dbx_path)

        # check if Dropbox rev is in local dict
        local_rev = self.get_local_rev(dbx_path)
        if local_rev is None:
            # If no, we have a conflict: files with the same name have been
            # created on Dropbox and locally inpedent from each other
            # If is file has been modified while the client was not running,
            # its entry from files_rev_dict is removed.
            logger.info("Conflicting local file without rev.")
            return 1
        # check if remote and local versions have same rev
        elif md.rev == local_rev:
            logger.info(
                    "Local file is the same as on Dropbox (rev %s). No download necessary.",
                    local_rev)
            return 2  # files are already the same

        elif not md.rev == local_rev:
            # we are dealing with different revisions, trust the Dropbox server version
            logger.info(
                    "Local file has rev %s, file on Dropbox has rev %s. Getting file from Dropbox.",
                    local_rev, md.rev)
            return 0
