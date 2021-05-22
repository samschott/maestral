# -*- coding: utf-8 -*-
"""This module contains the classes to coordinate sync threads."""

# system imports
import errno
import time
import gc
import ctypes
from contextlib import contextmanager
from functools import wraps
from queue import Empty, Queue
from threading import Event, RLock, Thread
from typing import Iterator, Optional, cast, Dict, List, Type, TypeVar, Callable, Any

# local imports
from . import __url__
from . import notify
from .client import DropboxClient
from .config import MaestralConfig
from .constants import (
    DISCONNECTED,
    CONNECTING,
    SYNCING,
    IDLE,
    PAUSED,
    CONNECTED,
)
from .database import SyncEvent
from .errors import (
    CancelledError,
    DropboxConnectionError,
    MaestralApiError,
    InotifyError,
)
from .sync import SyncEngine
from .fsevents import Observer
from .logging import scoped_logger
from .utils import exc_info_tuple
from .utils.integration import check_connection, get_inotify_limits


__all__ = ["SyncManager"]


FT = TypeVar("FT", bound=Callable[..., Any])


malloc_trim: Optional[Callable]

try:
    libc = ctypes.CDLL("libc.so.6")
    malloc_trim = libc.malloc_trim
except (OSError, AttributeError):
    malloc_trim = None


def _free_memory() -> None:
    """Give back memory"""

    gc.collect()

    if malloc_trim:
        malloc_trim(0)


class SyncManager:
    """Class to manage sync threads

    :param client: The Dropbox API client, a wrapper around the Dropbox Python SDK.
    """

    added_item_queue: "Queue[str]"
    """Queue of dropbox paths which have been newly included in syncing."""

    def __init__(self, client: DropboxClient):

        self.client = client
        self.config_name = self.client.config_name
        self._conf = MaestralConfig(self.config_name)
        self._logger = scoped_logger(__name__, self.config_name)

        self._lock = RLock()

        self.running = Event()
        self.startup_completed = Event()
        self.autostart = Event()

        self.added_item_queue = Queue()

        self.sync = SyncEngine(self.client)

        self._startup_time = -1.0

        self.connection_check_interval = 10
        self.connected = False
        self._connection_helper_running = True
        self.connection_helper = Thread(
            target=self.connection_monitor,
            name="maestral-connection-helper",
            daemon=True,
        )
        self.connection_helper.start()

        self.local_observer_thread: Optional[Observer] = None

    def _with_lock(fn: FT) -> FT:  # type: ignore
        @wraps(fn)
        def wrapper(self, *args, **kwargs):
            with self._lock:
                return fn(self, *args, **kwargs)

        return cast(FT, wrapper)

    # ---- config and state ------------------------------------------------------------

    @property
    def reindex_interval(self) -> float:
        """
        Interval in sec for period reindexing. Changes will be saved to state file.
        """
        return self._conf.get("sync", "reindex_interval")

    @reindex_interval.setter
    def reindex_interval(self, interval: float) -> None:
        """Setter: reindex_interval"""
        self._conf.set("sync", "reindex_interval", interval)

    @property
    def activity(self) -> Dict[str, SyncEvent]:
        """Returns a list all items queued for or currently syncing."""
        return self.sync.syncing

    @property
    def history(self) -> List[SyncEvent]:
        """A list of the last SyncEvents in our history. History will be kept for the
        interval specified by the config value ``keep_history`` (defaults to two weeks)
        but at most 1,000 events will kept."""
        return self.sync.history

    @property
    def idle_time(self) -> float:
        """
        Returns the idle time in seconds since the last file change or since startup if
        there haven't been any changes in our current session.
        """

        now = time.time()
        time_since_startup = now - self._startup_time
        time_since_last_sync = now - self.sync.last_change

        return min(time_since_startup, time_since_last_sync)

    # ---- control methods -------------------------------------------------------------

    @_with_lock
    def start(self) -> None:
        """Creates observer threads and starts syncing."""

        if self.running.is_set():
            return

        if not check_connection("www.dropbox.com"):
            # Schedule autostart when connection becomes available.
            self.autostart.set()
            self._logger.info(CONNECTING)
            return

        # create a new set of events to let old threads die down
        self.running = Event()
        self.startup_completed = Event()

        self.startup_thread = Thread(
            target=self.startup_worker,
            daemon=True,
            args=(
                self.running,
                self.startup_completed,
                self.autostart,
            ),
            name="maestral-sync-startup",
        )

        if self._conf.get("sync", "download"):

            self.download_thread = Thread(
                target=self.download_worker,
                daemon=True,
                args=(
                    self.running,
                    self.startup_completed,
                    self.autostart,
                ),
                name="maestral-download",
            )

            self.download_thread_added_folder = Thread(
                target=self.download_worker_added_item,
                daemon=True,
                args=(
                    self.running,
                    self.startup_completed,
                    self.autostart,
                ),
                name="maestral-folder-download",
            )

        if self._conf.get("sync", "upload"):

            self.upload_thread = Thread(
                target=self.upload_worker,
                daemon=True,
                args=(
                    self.running,
                    self.startup_completed,
                    self.autostart,
                ),
                name="maestral-upload",
            )

            if not self.local_observer_thread:
                self.local_observer_thread = self._create_observer()

        self.running.set()
        self.autostart.set()

        if self._conf.get("sync", "upload"):
            self.sync.fs_events.enable()
            self.upload_thread.start()

        if self._conf.get("sync", "download"):
            self.download_thread.start()
            self.download_thread_added_folder.start()

        self.startup_thread.start()

        self._startup_time = time.time()

    def _create_observer(self) -> Observer:

        local_observer_thread = Observer(timeout=40)
        local_observer_thread.setName("maestral-fsobserver")
        local_observer_thread.schedule(
            self.sync.fs_events, self.sync.dropbox_path, recursive=True
        )

        for emitter in local_observer_thread.emitters:
            # there should be only a single emitter thread
            emitter.name = "maestral-fsemitter"

        try:
            local_observer_thread.start()
        except OSError as exc:

            err_cls: Type[MaestralApiError]

            if exc.errno in (errno.ENOSPC, errno.EMFILE):
                title = "Inotify limit reached"

                try:
                    max_user_watches, max_user_instances, _ = get_inotify_limits()
                except OSError:
                    max_user_watches, max_user_instances = 2 ** 18, 2 ** 9

                url = f"{__url__}/docs/inotify-limits"

                if exc.errno == errno.ENOSPC:
                    n_new = max(2 ** 19, 2 * max_user_watches)

                    msg = (
                        "Changes to your Dropbox folder cannot be monitored because it "
                        "contains too many items. Please increase "
                        f"fs.inotify.max_user_watches to {n_new}. See {url} for more "
                        "information."
                    )

                else:
                    n_new = max(2 ** 10, 2 * max_user_instances)

                    msg = (
                        "Changes to your Dropbox folder cannot be monitored because "
                        "there are too many activity inotify instances. Please "
                        f"increase fs.inotify.max_user_instances to {n_new}. See "
                        f"{url} for more information."
                    )

                err_cls = InotifyError

            elif exc.errno in (errno.EPERM, errno.EACCES):
                title = "Insufficient permissions to monitor local changes"
                msg = "Please check the permissions for your local Dropbox folder"
                err_cls = InotifyError

            else:
                title = "Could not start watch of local directory"
                msg = exc.strerror
                err_cls = MaestralApiError

            new_error = err_cls(title, msg)
            self._logger.error(title, exc_info=exc_info_tuple(new_error))
            self.sync.desktop_notifier.notify(title, msg, level=notify.ERROR)

        return local_observer_thread

    @_with_lock
    def stop(self) -> None:
        """Stops syncing and destroys worker threads."""

        if self.running.is_set():
            self._logger.info("Shutting down threads...")

        self.sync.fs_events.disable()
        self.running.clear()
        self.startup_completed.clear()
        self.autostart.clear()

        self.sync.cancel_sync()

        if self.local_observer_thread:
            self.local_observer_thread.stop()
            self.local_observer_thread = None

        self._logger.info(PAUSED)

    def reset_sync_state(self) -> None:
        """Resets all saved sync state. Settings are not affected."""

        if self.running.is_set():
            raise RuntimeError("Cannot reset sync state while syncing.")

        self.sync.reset_sync_state()

    def rebuild_index(self) -> None:
        """
        Rebuilds the rev file by comparing remote with local files and updating rev
        numbers from the Dropbox server. Files are compared by their content hashes and
        conflicting copies are created if the contents differ. File changes during the
        rebuild process will be queued and uploaded once rebuilding has completed.

        Rebuilding will be performed asynchronously.
        """

        self._logger.info("Rebuilding index...")

        was_running = self.running.is_set()

        self.stop()

        self.sync.remote_cursor = ""
        self.sync.clear_index()

        if was_running:
            self.start()

    # ---- thread methods --------------------------------------------------------------

    def connection_monitor(self) -> None:
        """
        Monitors the connection to Dropbox servers. Pauses syncing when the connection
        is lost and resumes syncing when reconnected and syncing has not been paused by
        the user.
        """

        while self._connection_helper_running:

            connected = check_connection("www.dropbox.com")

            if connected != self.connected:
                # Log the status change.
                self._logger.info(CONNECTED if connected else CONNECTING)

            if connected:
                if not self.running.is_set() and self.autostart.is_set():
                    self.start()

            self.connected = connected

            time.sleep(self.connection_check_interval)

    def download_worker(
        self,
        running: Event,
        startup_completed: Event,
        autostart: Event,
    ) -> None:
        """
        Worker to sync changes of remote Dropbox with local folder.

        :param running: Event to shutdown local file event handler and worker threads.
        :param startup_completed: Set when startup sync is completed.
        :param autostart: Set when syncing should automatically resume on connection.
        """

        startup_completed.wait()

        while running.is_set():

            with self._handle_sync_thread_errors(running, autostart):

                with self.sync.client.clone_with_new_session() as client:

                    has_changes = self.sync.wait_for_remote_changes(
                        self.sync.remote_cursor, client=client
                    )

                    if not running.is_set():
                        return

                    self.sync.ensure_dropbox_folder_present()

                    if has_changes:
                        self._logger.info(SYNCING)
                        self.sync.download_sync_cycle(client)
                        self._logger.info(IDLE)

                        client.get_space_usage()  # update space usage

        _free_memory()

    def download_worker_added_item(
        self,
        running: Event,
        startup_completed: Event,
        autostart: Event,
    ) -> None:
        """
        Worker to download items which have been newly included in sync.

        :param running: Event to shutdown local file event handler and worker threads.
        :param startup_completed: Set when startup sync is completed.
        :param autostart: Set when syncing should automatically resume on connection.
        """

        startup_completed.wait()

        while running.is_set():

            with self._handle_sync_thread_errors(running, autostart):

                try:
                    dbx_path = self.added_item_queue.get(timeout=40)
                except Empty:
                    pass
                else:
                    # protect against crashes
                    self.sync.pending_downloads.add(dbx_path.lower())

                    if not running.is_set():
                        return

                    with self.sync.sync_lock:

                        with self.sync.client.clone_with_new_session() as client:
                            self.sync.get_remote_item(dbx_path, client)

                        self.sync.pending_downloads.discard(dbx_path)

                        self._logger.info(IDLE)

        _free_memory()

    def upload_worker(
        self,
        running: Event,
        startup_completed: Event,
        autostart: Event,
    ) -> None:
        """
        Worker to sync local changes to remote Dropbox.

        :param running: Event to shutdown local file event handler and worker threads.
        :param startup_completed: Set when startup sync is completed.
        :param autostart: Set when syncing should automatically resume on connection.
        """

        startup_completed.wait()

        while running.is_set():

            with self._handle_sync_thread_errors(running, autostart):

                has_changes = self.sync.wait_for_local_changes()

                if not running.is_set():
                    return

                self.sync.ensure_dropbox_folder_present()

                if has_changes:
                    self._logger.info(SYNCING)
                    self.sync.upload_sync_cycle()
                    self._logger.info(IDLE)

        _free_memory()

    def startup_worker(
        self,
        running: Event,
        startup_completed: Event,
        autostart: Event,
    ) -> None:
        """
        Worker to sync local changes to remote Dropbox.

        :param running: Event to shutdown local file event handler and worker threads.
        :param startup_completed: Set when startup sync is completed.
        :param autostart: Set when syncing should automatically resume on connection.
        """

        with self._handle_sync_thread_errors(running, autostart):

            # Reload mignore rules.
            self.sync.load_mignore_file()

            with self.sync.client.clone_with_new_session() as client:

                # Retry failed downloads.
                if len(self.sync.download_errors) > 0:
                    self._logger.info("Retrying failed syncs...")

                for dbx_path in list(self.sync.download_errors):
                    self.sync.get_remote_item(dbx_path, client)

                # Resume interrupted downloads.
                if len(self.sync.pending_downloads) > 0:
                    self._logger.info("Resuming interrupted syncs...")

                for dbx_path in list(self.sync.pending_downloads):
                    self.sync.get_remote_item(dbx_path, client)
                    self.sync.pending_downloads.discard(dbx_path)

                if not running.is_set():
                    startup_completed.set()
                    return

                self.sync.download_sync_cycle(client)

            if not running.is_set():
                startup_completed.set()
                return

            if self._conf.get("sync", "upload"):
                self.sync.upload_local_changes_while_inactive()

            self._logger.info(IDLE)

        startup_completed.set()
        _free_memory()

    # ---- utilities -------------------------------------------------------------------

    @contextmanager
    def _handle_sync_thread_errors(
        self, running: Event, autostart: Event
    ) -> Iterator[None]:

        try:
            yield
        except CancelledError:
            running.clear()
        except DropboxConnectionError:
            self._logger.info(DISCONNECTED)
            self._logger.debug("Connection error", exc_info=True)
            running.clear()
            autostart.set()
            self._logger.info(CONNECTING)
        except Exception as err:
            running.clear()
            autostart.clear()
            title = getattr(err, "title", "Unexpected error")
            message = getattr(err, "message", "Please restart to continue syncing")
            self._logger.error(title, exc_info=True)
            self.sync.desktop_notifier.notify(title, message, level=notify.ERROR)

    def __del__(self):
        try:
            self.stop()
            self._connection_helper_running = False
        except Exception:
            pass
