import os.path as osp
import logging
import time
import threading
from dropbox import files

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from config.main import CONF, SUBFOLDER
from config.base import get_conf_path

configurationDirectory = get_conf_path(SUBFOLDER)
dropbox_path = CONF.get('main', 'path')

logger = logging.getLogger(__name__)
lock = threading.RLock()

EXCLUDED_FILES = CONF.get('main', 'exlcuded_files')
EXCLUDED_FOLDERS = CONF.get('main', 'excluded_folders')


def is_excluded(dbx_path):
    """Check if file is excluded from sync

    Checks if a file or folder has been excluded by the user, or if it  is
    temporary and created only during a save event.
    :param dbx_path: string containing Dropbox path
    :returns: True if file excluded, False otherwise.
    """
    excluded = False
    if osp.basename(dbx_path) in EXCLUDED_FILES:
        excluded = True

    for excluded_folder in EXCLUDED_FOLDERS:
        if not osp.commonpath([dbx_path, excluded_folder]) in [osp.sep, ""]:
            excluded = True

    if dbx_path.count('.') > 1:  # ignore ephemeral files on macOS
        excluded = True

    return excluded


def local_sync(func):
    """Wrapper for methods and detect and sync local file changes.

    - Aborts if file or folder has been excluded by user, or if file temporary
      and created only during a save event.
    - Pauses the remote monitor for the duration of the local sync / upload.
    - Updates the lastsync time in the config file.
    """

    def wrapper(*args, **kwargs):
        if is_excluded(args[1].src_path):
            return

        if hasattr(args[1], 'dst_path'):
            if is_excluded(args[1].dst_path):
                return

        print('syncing...')
        args[0].remote_monitor.stop()
        with lock:
            result = func(*args, **kwargs)
        args[0].remote_monitor.start()
        CONF.set('internal', 'lastsync', time.time())
        print('done')
        return result
    return wrapper


class LoggingEventHandler(FileSystemEventHandler):
    """Logs all the events captured."""

    def on_moved(self, event):
        super(LoggingEventHandler, self).on_moved(event)
        logger.info("Move detected: from '%s' to '%s'", event.src_path, event.dest_path)

    def on_created(self, event):
        super(LoggingEventHandler, self).on_created(event)
        logger.info("Creation detected: '%s'", event.src_path)

    def on_deleted(self, event):
        super(LoggingEventHandler, self).on_deleted(event)
        logger.info("Deletion detected: '%s'", event.src_path)

    def on_modified(self, event):
        super(LoggingEventHandler, self).on_modified(event)
        logger.info("Modification detected: '%s'", event.src_path)


class DropboxEventHandler(LoggingEventHandler):
    """Logs all the events captured."""

    def __init__(self, client, remote_monitor):

        self.client = client
        self.remote_monitor = remote_monitor

    @local_sync
    def on_moved(self, event):
        super(LoggingEventHandler, self).on_moved(event)

        path = event.src_path
        path2 = event.dest_path

        dbx_path = self.client.to_dbx_path(path)
        dbx_path2 = self.client.to_dbx_path(path2)

        # If the file name contains multiple periods it is likely a temporary
        # file created during a saving event on macOS. Irgnore such files.
        if osp.basename(path2).count('.') > 1:
            return

        self.client.move(dbx_path, dbx_path2)

        what = 'directory' if event.is_directory else 'file'
        logger.info("Moved %s: from %s to %s", what, dbx_path, dbx_path2)

    @local_sync
    def on_created(self, event):
        super(LoggingEventHandler, self).on_created(event)

        what = 'directory' if event.is_directory else 'file'

        path = event.src_path
        dbx_path = self.client.to_dbx_path(path)

        if what == 'file':

            if osp.isfile(path):
                while True:  # wait until file is fully created
                    size1 = osp.getsize(path)
                    time.sleep(0.5)
                    size2 = osp.getsize(path)
                    if size1 == size2:
                        break

                rev = self.client.get_local_rev(dbx_path)
                # if truly a new file
                if rev is None:
                    mode = files.WriteMode('add')
                # or a 'flase' new file event triggered by modification
                # e.g., some programms create backup files and then swap them
                # in to replace the files you are editing on the disk
                else:
                    mode = files.WriteMode('update', rev)
                md = self.client.upload(path, dbx_path, autorename=True, mode=mode)

                logger.info("Created %s: %s (rev %s)", what, md.path_display, md.rev)

        else:
            what = 'directory' if event.is_directory else 'file'
            if what == 'directory':
                md = self.client.make_dir(dbx_path, autorename=True)
                logger.info("Created %s: %s", what, md.path_display)

    @local_sync
    def on_deleted(self, event):
        super(LoggingEventHandler, self).on_deleted(event)

        path = event.src_path
        dbx_path = self.client.to_dbx_path(path)
        what = 'directory' if event.is_directory else 'file'
        md = self.client.remove(dbx_path)
        logger.info("Deleted %s.", what)

    @local_sync
    def on_modified(self, event):
        super(LoggingEventHandler, self).on_modified(event)

        what = 'directory' if event.is_directory else 'file'
        path = event.src_path
        dbx_path = self.client.to_dbx_path(path)

        if what == "file":
            if osp.isfile(path):

                while True:  # wait until file is fully created
                    size1 = osp.getsize(path)
                    time.sleep(0.2)
                    size2 = osp.getsize(path)
                    if size1 == size2:
                        break

                rev = self.client.get_local_rev(dbx_path)
                mode = files.WriteMode('update', rev)
                md = self.client.upload(path, dbx_path, autorename=True, mode=mode)
                logger.info("Modified %s: %s (old rev: %s, new rev %s)", what,
                            md.path_display, rev, md.rev)


class GetRemoteChangesThread(threading.Thread):

    pause_event = threading.Event()
    stop_event = threading.Event()

    def __init__(self, client):
        super(self.__class__, self).__init__()
        self.client = client

    def run(self):
        while not self.stop_event.is_set():
            while self.pause_event.is_set():
                time.sleep(0.1)
            changes = self.client.wait_for_remote_changes()
            while self.pause_event.is_set():
                time.sleep(0.1)

            if changes:
                with lock:
                    self.client.get_remote_changes()

    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.pause_event.clear()

    def stop(self):
        self.stop_event.set()


class RemoteDummy(object):

    def start(self):
        pass

    def stop(self):
        pass


class RemoteMonitor(object):

    def __init__(self, client):

        self.client = client

        self.thread = GetRemoteChangesThread(self.client)
        self.thread.pause()
        self.thread.start()

    def start(self):
        """Start observation of remote Dropbox folder."""
        self.thread.resume()

    def stop(self):
        """Stop observation of remote Dropbox folder."""
        self.thread.pause()

    def __del__(self):
        try:
            self.thread.stop()
        except AttributeError:
            pass


class LocalMonitor(object):

    def __init__(self, client, remote_monitor=RemoteDummy()):

        self.client = client
        self.remote_monitor = remote_monitor

        self.event_handler = DropboxEventHandler(self.client, self.remote_monitor)

    def upload_local_changes_after_inactive(self):
        """Push changes while client has not been running to Dropbox."""

        events = self.client.get_local_changes()

        for event in events:
            if event.event_type is 'created':
                self.event_handler.on_created(event)
            elif event.event_type is 'deleted':
                self.event_handler.on_deleted(event)
            elif event.event_type is 'modified':
                self.event_handler.on_modified(event)

    def start(self):
        """Start observation of local Dropbox folder."""
        self.observer = Observer()
        self.observer.schedule(self.event_handler, dropbox_path, recursive=True)
        self.observer.start()

    def stop(self):
        """Stop observation of local Dropbox folder."""
        self.observer.stop()
        self.observer.join()
