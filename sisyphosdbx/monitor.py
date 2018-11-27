import os.path as osp
import logging
import time
import threading
from queue import Queue
from dropbox import files

from watchdog.observers import Observer
from watchdog.events import (FileSystemEventHandler, EVENT_TYPE_CREATED,
                             EVENT_TYPE_DELETED, EVENT_TYPE_MODIFIED,
                             EVENT_TYPE_MOVED)

from sisyphosdbx.config.main import CONF, SUBFOLDER
from sisyphosdbx.config.base import get_conf_path

configurationDirectory = get_conf_path(SUBFOLDER)
dropbox_path = CONF.get('main', 'path')

logger = logging.getLogger(__name__)
lock = threading.Lock()


def local_sync(func):
    """Wrapper for methods and detect and sync local file changes.

    - Aborts if file or folder has been excluded by user, or if file temporary
      and created only during a save event.
    - Pauses the remote monitor for the duration of the local sync / upload.
    - Updates the lastsync time in the config file.
    """

    def wrapper(*args, **kwargs):

        args[0].remote_monitor.stop()
        try:
            with lock:
                func(*args, **kwargs)
        except Exception as err:
            logger.error(err)

        args[0].remote_monitor.start()

        CONF.set('internal', 'lastsync', time.time())

    return wrapper


class TimedQueue(Queue):

    def __init__(self):
        super(self.__class__, self).__init__()

        self.update_time = 0

    # Put a new item in the queue, remember time
    def _put(self, item):
        self.queue.append(item)
        self.update_time = time.time()


class FileEventHandler(FileSystemEventHandler):
    """Logs all the events captured."""

    event_q = TimedQueue()

    def on_moved(self, event):
        logger.info("Move detected: from '%s' to '%s'", event.src_path, event.dest_path)
        self.event_q.put(event)

    def on_created(self, event):
        logger.info("Creation detected: '%s'", event.src_path)
        self.event_q.put(event)

    def on_deleted(self, event):
        logger.info("Deletion detected: '%s'", event.src_path)
        self.event_q.put(event)

    def on_modified(self, event):
        logger.info("Modification detected: '%s'", event.src_path)
        self.event_q.put(event)


class DropboxEventHandler(object):
    """Logs all the events captured."""

    def __init__(self, client):

        self.client = client

    def on_moved(self, event):

        path = event.src_path
        path2 = event.dest_path

        dbx_path = self.client.to_dbx_path(path)
        dbx_path2 = self.client.to_dbx_path(path2)

        # is file excluded?
        if self.client.is_excluded(dbx_path2):
            return

        # If the file name contains multiple periods it is likely a temporary
        # file created during a saving event on macOS. Irgnore such files.
        if osp.basename(path2).count('.') > 1:
            return

        self.client.move(dbx_path, dbx_path2)

    def on_created(self, event):
        path = event.src_path
        dbx_path = self.client.to_dbx_path(path)

        # is file excluded?
        if self.client.is_excluded(dbx_path):
            return

        # has event just been triggere by remote_monitor?
        print(self.client.flagged)
        print(dbx_path)
        print(dbx_path in self.client.flagged)
        if dbx_path in self.client.flagged:
            logging.info("'%s' has just been synced. Nothing to do.", dbx_path)
            self.client.flagged.remove(dbx_path)
            return

        if not event.is_directory:

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
                # or a 'false' new file event triggered by saving the file
                # e.g., some programms create backup files and then swap them
                # in to replace the files you are editing on the disk
                else:
                    mode = files.WriteMode('update', rev)
                self.client.upload(path, dbx_path, autorename=True, mode=mode)

        elif event.is_directory:
            result = self.client.list_folder(dbx_path)
            if result is not None:
                # directory is already on Dropbox
                return
            else:
                self.client.make_dir(dbx_path)

    def on_deleted(self, event):
        path = event.src_path
        dbx_path = self.client.to_dbx_path(path)

        # is file excluded?
        if self.client.is_excluded(dbx_path):
            return

        # has event just been triggere by remote_monitor?
        if dbx_path in self.client.flagged:
            self.client.flagged.remove(dbx_path)
            return

        rev = self.client.get_local_rev(dbx_path)
        if rev is not None:
            self.client.remove(dbx_path)

    def on_modified(self, event):
        path = event.src_path
        dbx_path = self.client.to_dbx_path(path)

        # is file excluded?
        if self.client.is_excluded(dbx_path):
            return

        # has event just been triggere by remote_monitor?
        if dbx_path in self.client.flagged:
            self.client.flagged.remove(dbx_path)
            return

        if not event.is_directory:  # ignore directory modified events
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
                logger.info("Modified file: %s (old rev: %s, new rev %s)",
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


class ProcessLocalChangesThread(threading.Thread):

    pause_event = threading.Event()
    stop_event = threading.Event()

    def __init__(self, dbx_handler, event_q):
        super(self.__class__, self).__init__()
        self.dbx_handler = dbx_handler
        self.event_q = event_q
        self.delay = 0.5

    def run(self):
        while not self.stop_event.is_set():
            # pause if instructed
            while self.pause_event.is_set():
                time.sleep(0.1)

            # any events to process?
            if not self.event_q.empty():
                # wait for self.delay after last event has been registered
                t0 = time.time()
                while t0 - self.event_q.update_time < self.delay:
                    time.sleep(self.delay)
                    t0 = time.time()

                # get all events after folder has been idle for self.delay
                events = []
                while self.event_q.qsize() > 0:
                    events.append(self.event_q.get())

                # check for folder move events
                def is_moved_folder(x):
                    is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
                    return is_moved_event and x.is_directory

                moved_fodler_events = [x for x in events if is_moved_folder(x)]

                # check for children of moved folders
                def is_moved_child(x, parent_event):
                    is_moved_event = (x.event_type is EVENT_TYPE_MOVED)
                    is_child = x.src_path.startswith(parent_event.src_path)
                    return is_moved_event and is_child

                child_move_events = []
                for parent_event in moved_fodler_events:
                    event = [x for x in events if is_moved_child(x, parent_event)]
                    child_move_events.append(event)

                # remove all child_move_events from events
                events = list(set(events) - set(child_move_events))

                # process all events:
                with lock:
                    for event in events:
                        if event.event_type is EVENT_TYPE_CREATED:
                            self.dbx_handler.on_created(event)
                        elif event.event_type is EVENT_TYPE_MOVED:
                            self.dbx_handler.on_moved(event)
                        elif event.event_type is EVENT_TYPE_DELETED:
                            self.dbx_handler.on_deleted(event)
                        elif event.event_type is EVENT_TYPE_MODIFIED:
                            self.dbx_handler.on_modified(event)

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

    def __init__(self, client):

        self.client = client

        self.file_handler = FileEventHandler()
        self.observer = Observer()
        self.observer.schedule(self.file_handler, dropbox_path, recursive=True)
        self.observer.start()

        self.dbx_handler = DropboxEventHandler(self.client)
        self.thread = ProcessLocalChangesThread(self.dbx_handler, self.file_handler.event_q)
        self.thread.pause()
        self.thread.start()

    def upload_local_changes_after_inactive(self):
        """Push changes while client has not been running to Dropbox."""

        events = self.client.get_local_changes()

        for event in events:
            if event.event_type is EVENT_TYPE_CREATED:
                self.dbx_handler.on_created(event)
            elif event.event_type is EVENT_TYPE_DELETED:
                self.dbx_handler.on_deleted(event)
            elif event.event_type is EVENT_TYPE_MODIFIED:
                self.dbx_handler.on_modified(event)

    def start(self):
        """Start processing of local Dropbox file events."""

        self.thread.resume()

    def stop(self):
        """Stop processing of local Dropbox file events."""
        self.thread.pause()

    def __del__(self):
        try:
            self.observer.stop()
        except AttributeError:
            pass

        try:
            self.observer.join()
        except AttributeError:
            pass

        try:
            self.thread.stop()
        except AttributeError:
            pass
