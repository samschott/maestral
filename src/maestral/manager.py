# -*- coding: utf-8 -*-
"""This module contains the classes to coordinate sync threads."""

# system imports
import os
import errno
import time
import gc
import ctypes
from contextlib import contextmanager
from functools import wraps
from queue import Empty, Queue
from threading import Event, RLock, Thread
from tempfile import TemporaryDirectory
from typing import Iterator, Optional, cast, Dict, List, Type, TypeVar, Callable, Any

# external imports
from dropbox.common import TeamRootInfo, UserRootInfo

# local imports
from . import __url__
from . import notify
from .client import DropboxClient
from .config import MaestralConfig, MaestralState
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
    NoDropboxDirError,
    PathRootError,
    DropboxServerError,
)
from .sync import SyncEngine
from .fsevents import Observer
from .logging import scoped_logger
from .utils import exc_info_tuple, removeprefix
from .utils.integration import check_connection, get_inotify_limits
from .utils.path import move, delete, is_equal_or_child, is_child, normalize


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
        self._state = MaestralState(self.config_name)
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
            raise MaestralApiError(
                "Cannot reset sync state while syncing", "Please try again when idle."
            )

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

        self.reset_sync_state()

        if was_running:
            self.start()

    # ---- path root management --------------------------------------------------------

    def check_and_update_path_root(self) -> bool:
        """
        Checks if the user's root namespace corresponds to the currently configured
        path root. Updates the root namespace if required and migrates the local
        folder structure. Syncing will be paused during the migration.

        :returns: Whether the path root was updated.
        """

        if self._needs_path_root_update():

            was_running = self.running.is_set()

            self.stop()

            self._update_path_root()

            if was_running:
                self._logger.info("Restarting sync")
                self.start()

            return True
        else:
            self._logger.debug("Path root is up to date")
            return False

    def _needs_path_root_update(self) -> bool:
        """
        Checks if the user's root namespace corresponds to the currently configured
        path root.

        :returns: Whether the configured root namespace needs to be updated.
        """

        self._logger.debug("Checking path root...")

        account_info = self.client.get_account_info()
        return self.client.namespace_id != account_info.root_info.root_namespace_id

    def _update_path_root(self) -> None:
        """
        Changes the layout of the local Dropbox folder if the user joins or leaves a
        team. New team folders will be downloaded, old team folders will be removed.
        """

        current_root_type = self._state.get("account", "path_root_type")
        current_user_home_path = self._state.get("account", "home_path")
        current_user_home_path_lower = normalize(current_user_home_path)

        if current_root_type == "team" and current_user_home_path == "":
            raise MaestralApiError(
                "Cannot migrate folder structure",
                "Inconsistent namespace information found.",
            )

        root_info = self.client.account_info.root_info
        team = self.client.account_info.team

        team_name = team.name if team else "team"

        if isinstance(root_info, UserRootInfo):
            new_root_type = "user"
            new_user_home_path = ""
        elif isinstance(root_info, TeamRootInfo):
            new_root_type = "team"
            new_user_home_path = root_info.home_path
        else:
            raise MaestralApiError(
                "Unknown root namespace type",
                f"Got {root_info!r} but expected UserRootInfo or TeamRootInfo.",
            )

        local_user_home_path = self.sync.to_local_path_from_cased(new_user_home_path)

        try:
            local_dropbox_dirlist = list(os.scandir(self.sync.dropbox_path))
        except (FileNotFoundError, NotADirectoryError):
            title = "Dropbox folder missing"
            msg = (
                "Please move the Dropbox folder back to its original location or "
                "restart Maestral to set up a new folder."
            )
            raise NoDropboxDirError(title, msg)

        with self.sync.sync_lock:

            if new_root_type == "team" and current_root_type == "user":

                # User joined a team.

                self._logger.info("User joined %s. Resyncing user files.", team_name)

                self.sync.desktop_notifier.notify(
                    f"Joined {team_name}",
                    "Migrating user files and downloading team folders",
                )

                # Migrate user folder to "self.sync.dropbox_path/home_path". We do this
                # by creating a temporary folder and renaming it after moving all
                # personal items to prevent name conflicts where a folder has the same
                # name as `root_info.home_path`.

                tmpdir = TemporaryDirectory(dir=self.sync.dropbox_path)

                for entry in local_dropbox_dirlist:
                    if entry.path != tmpdir.name:
                        new_path = f"{tmpdir.name}/{entry.name}"
                        self._logger.debug(
                            "Moving to personal folder: %r → %r", entry.path, new_path
                        )
                        move(entry.path, new_path, raise_error=True)

                self._logger.debug("Moving %r → %r", tmpdir.name, local_user_home_path)
                os.rename(tmpdir.name, local_user_home_path)

                # Migrate all excluded items.
                self._logger.debug("Migrating excluded items")
                new_excluded = [
                    root_info.home_path + path for path in self.sync.excluded_items
                ]
                self.sync.excluded_items = new_excluded

            elif new_root_type == "user" and current_root_type == "team":

                # User left a team.

                self._logger.info("User left team. Updating folder layout.")

                self.sync.desktop_notifier.notify(
                    "Left Dropbox Team",
                    "Migrating user files and removing team folders",
                )

                # Remove all team folders.
                for entry in local_dropbox_dirlist:
                    if entry.name != current_user_home_path.lstrip("/"):
                        delete(entry.path, raise_error=True)

                # Migrate user folders to local Dropbox root. We do this by renaming the
                # user home to a temporary name and then moving its contents to the
                # parent folder.

                old_home_root = self.sync.dropbox_path + current_user_home_path

                tmpdir = TemporaryDirectory(dir=self.sync.dropbox_path)

                try:
                    os.rename(old_home_root, tmpdir.name)
                except (FileNotFoundError, NotADirectoryError):
                    # User folder does not (yet) exist.
                    pass
                else:
                    for entry in list(os.scandir(tmpdir.name)):
                        new_path = f"{self.sync.dropbox_path}/{entry.name}"
                        move(entry.path, new_path, raise_error=True)
                        self._logger.debug(
                            "Moved to root folder: %r → %r", entry.path, new_path
                        )

                delete(tmpdir.name)

                # Migrate excluded items:
                # Prune all teams folders from excluded list. Remove home folder
                # prefix from excluded items. If the user folder itself is
                # excluded, keep it excluded.
                self._logger.debug("Migrating excluded items")
                new_excluded = [
                    removeprefix(path, current_user_home_path_lower)
                    for path in self.sync.excluded_items
                    if is_child(path, current_user_home_path_lower)
                ]

                self.sync.excluded_items = new_excluded

            elif new_root_type == "team" and current_root_type == "team":

                # User switched between different teams.

                self._logger.info("User switched teams. Updating team folders.")

                self.sync.desktop_notifier.notify(
                    f"Switched teams to {team_name}", "Updating team folders"
                )

                # Remove all team folders, leave user folder alone.
                for entry in local_dropbox_dirlist:
                    if entry.name != current_user_home_path.lstrip("/"):
                        delete(entry.path, raise_error=True)
                        self._logger.debug("Deleted team folder: %r", entry.path)

                # Migrate excluded items:
                # Prune all teams folders from excluded list. If the user folder
                # itself is excluded, keep it excluded.
                self._logger.debug("Migrating excluded items")
                new_excluded = [
                    path
                    for path in self.sync.excluded_items
                    if is_equal_or_child(path, current_user_home_path_lower)
                ]
                self.sync.excluded_items = new_excluded

            # Update path root of client.
            self.client.update_path_root(root_info)

            #  Trigger reindex.
            self.sync.reset_sync_state()

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

                    # Check for root namespace updates. Don't apply any remote
                    # changes in case of a changed root path.
                    if self.check_and_update_path_root():
                        return

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

            # Fail early if Dropbox folder disappeared.
            self.sync.ensure_dropbox_folder_present()

            # Reload mignore rules.
            self.sync.load_mignore_file()

            self.client.get_space_usage()

            # Update path root and migrate local folders. This is required when a user
            # joins or leaves a team and their root namespace changes.
            self.check_and_update_path_root()

            if not running.is_set():
                startup_completed.set()
                return

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
            # Shutdown will be handled externally.
            running.clear()
        except (DropboxConnectionError, DropboxServerError):
            self._logger.debug("Connection error", exc_info=True)
            self._logger.info(DISCONNECTED)
            self.stop()
            self.autostart.set()
            self._logger.info(CONNECTING)
        except PathRootError:
            self._logger.debug("API call failed due to path root error", exc_info=True)
            self.check_and_update_path_root()
        except Exception as err:
            title = getattr(err, "title", "Unexpected error")
            message = getattr(err, "message", "Please restart to continue syncing")
            self._logger.error(title, exc_info=True)
            self.sync.desktop_notifier.notify(title, message, level=notify.ERROR)
            self.stop()

    def __del__(self):
        try:
            self.stop()
            self._connection_helper_running = False
        except Exception:
            pass
