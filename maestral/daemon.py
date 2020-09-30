# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under the MIT licence.

This module defines functions to start and stop the sync daemon and retrieve proxy objects
for a running daemon.

"""

# system imports
import sys
import os
import time
import signal
import traceback
import enum
import subprocess
from shlex import quote
import threading
import fcntl
import struct
import tempfile
import itertools
import asyncio
import logging
from typing import Optional, Union, Tuple, Dict, Type, TYPE_CHECKING
from types import TracebackType, FrameType

# external imports
import Pyro5  # type: ignore
from Pyro5.errors import CommunicationError  # type: ignore
from Pyro5.api import Daemon, Proxy, expose, oneway, register_dict_to_class  # type: ignore
import sdnotify  # type: ignore
from fasteners import InterProcessLock  # type: ignore

# local imports
from maestral.errors import SYNC_ERRORS, FATAL_ERRORS, MaestralApiError
from maestral.constants import IS_FROZEN, IS_MACOS
from maestral.utils.appdirs import get_runtime_path


if TYPE_CHECKING:
    from maestral.main import Maestral


logger = logging.getLogger(__name__)
sd_notifier = sdnotify.SystemdNotifier()


# systemd environment
INVOCATION_ID = os.getenv("INVOCATION_ID")
NOTIFY_SOCKET = os.getenv("NOTIFY_SOCKET")
WATCHDOG_PID = os.getenv("WATCHDOG_PID")
WATCHDOG_USEC = os.getenv("WATCHDOG_USEC")
IS_WATCHDOG = WATCHDOG_USEC and (
    WATCHDOG_PID is None or int(WATCHDOG_PID) == os.getpid()
)


URI = "PYRO:maestral.{0}@{1}"
Pyro5.config.THREADPOOL_SIZE_MIN = 2

MaestralProxyType = Union["Maestral", Proxy]


def freeze_support() -> None:
    """Freeze support for multiprocessing and daemon startup. This works by checking for
    '--multiprocessing-fork' and '--frozen-daemon' command line arguments. Call this
    function at the entry point of the executable, as soon as possible and ideally before
    any heavy imports."""

    import argparse
    import multiprocessing as mp

    mp.freeze_support()

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-c", "--config-name", default="maestral")
    parser.add_argument("--frozen-daemon", action="store_true")
    parsed_args, remaining = parser.parse_known_args()

    if parsed_args.frozen_daemon:
        start_maestral_daemon(parsed_args.config_name)
        sys.exit()


class Stop(enum.Enum):
    """
    Enumeration of daemon exit results.

    :cvar Ok: Daemon quit successfully.
    :cvar Killed: Daemon process was killed.
    :cvar NotRunning: Daemon was not running.
    :cvar Failed: Could not shut down daemon.
    """

    Ok = 0
    Killed = 1
    NotRunning = 2
    Failed = 3


class Start(enum.Enum):
    """
    Enumeration of daemon start results.

    :cvar Ok: Daemon started successfully.
    :cvar AlreadyRunning: Daemon was already running.
    :cvar Failed: Could not start daemon.
    """

    Ok = 0
    AlreadyRunning = 1
    Failed = 2


# ==== error serialization ===============================================================


def serpent_deserialize_api_error(class_name: str, d: dict) -> MaestralApiError:
    """
    Deserializes a :class:`errors.MaestralApiError`.

    :param class_name: Name of class to deserialize.
    :param d: Dictionary of serialized class.
    :returns: Class instance.
    """
    # import maestral errors for evaluation
    import maestral.errors  # noqa: F401

    cls = eval(class_name)
    err = cls(*d["args"])
    for a_name, a_value in d["attributes"].items():
        setattr(err, a_name, a_value)

    return err


for err_cls in itertools.chain(SYNC_ERRORS, FATAL_ERRORS):
    register_dict_to_class(
        err_cls.__module__ + "." + err_cls.__name__, serpent_deserialize_api_error
    )


# ==== interprocess locking ==============================================================


def _get_lockdata() -> Tuple[bytes, str, int]:

    try:
        os.O_LARGEFILE
    except AttributeError:
        start_len = "ll"
    else:
        start_len = "qq"

    if (
        sys.platform.startswith(("netbsd", "freebsd", "openbsd"))
        or sys.platform == "darwin"
    ):
        if struct.calcsize("l") == 8:
            off_t = "l"
            pid_t = "i"
        else:
            off_t = "lxxxx"
            pid_t = "l"

        fmt = off_t + off_t + pid_t + "hh"
        pid_index = 2
        lockdata = struct.pack(fmt, 0, 0, 0, fcntl.F_WRLCK, 0)
    # elif sys.platform.startswith('gnukfreebsd'):
    #     fmt = 'qqihhi'
    #     pid_index = 2
    #     lockdata = struct.pack(fmt, 0, 0, 0, fcntl.F_WRLCK, 0, 0)
    # elif sys.platform in ('hp-uxB', 'unixware7'):
    #     fmt = 'hhlllii'
    #     pid_index = 2
    #     lockdata = struct.pack(fmt, fcntl.F_WRLCK, 0, 0, 0, 0, 0, 0)
    elif sys.platform.startswith("linux"):
        fmt = "hh" + start_len + "ih"
        pid_index = 4
        lockdata = struct.pack(fmt, fcntl.F_WRLCK, 0, 0, 0, 0, 0)
    else:
        raise RuntimeError(f"Unsupported platform {sys.platform}")

    return lockdata, fmt, pid_index


class Lock:
    """
    A inter-process and inter-thread lock. This reuses uses code from oslo.concurrency
    but provides non-blocking acquire. Use the :meth:`singleton` class method to retrieve
    an existing instance for thread-safe usage.
    """

    _instances: Dict[str, "Lock"] = dict()
    _singleton_lock = threading.Lock()

    @classmethod
    def singleton(cls, name: str, lock_path: Optional[str] = None) -> "Lock":
        """
        Retrieve an existing lock object with a given 'name' or create a new one. Use this
        method for thread-safe locks.

        :param name: Name of lock file.
        :param lock_path: Directory for lock files. Defaults to the temporary directory
            returned by :func:`tempfile.gettempdir()` if not given.
        """

        with cls._singleton_lock:
            try:
                instance = cls._instances[name]
            except KeyError:
                instance = cls(name, lock_path)
                cls._instances[name] = instance

            return instance

    def __init__(self, name: str, lock_path: Optional[str] = None) -> None:

        self.name = name
        dirname = lock_path or tempfile.gettempdir()
        lock_path = os.path.join(dirname, name)

        self._internal_lock = threading.Semaphore()
        self._external_lock = InterProcessLock(lock_path)

        self._lock = threading.RLock()

    def acquire(self) -> bool:
        """
        Attempts to acquire the given lock.

        :returns: Whether or not the acquisition succeeded.
        """

        with self._lock:
            locked_internal = self._internal_lock.acquire(blocking=False)

            if not locked_internal:
                return False

            try:
                locked_external = self._external_lock.acquire(blocking=False)
            except Exception:
                self._internal_lock.release()
                raise
            else:

                if locked_external:
                    return True
                else:
                    self._internal_lock.release()
                    return False

    def release(self) -> None:
        """Release the previously acquired lock."""
        with self._lock:
            self._external_lock.release()
            self._internal_lock.release()

    def locked(self) -> bool:
        """Checks if the lock is currently held by any thread or process."""
        with self._lock:
            gotten = self.acquire()
            if gotten:
                self.release()
            return not gotten

    def locking_pid(self) -> Optional[int]:
        """
        Returns the PID of the process which currently holds the lock or ``None``. This
        should work on macOS, OpenBSD and Linux but may fail on some platforms. Always use
        :meth:`locked` to check if the lock is held by any process.

        :returns: The PID of the process which currently holds the lock or ``None``.
        """

        with self._lock:

            if self._external_lock.acquired:
                return os.getpid()

            try:
                # don't close again in case we are the locking process
                self._external_lock._do_open()
                lockdata, fmt, pid_index = _get_lockdata()
                lockdata = fcntl.fcntl(
                    self._external_lock.lockfile, fcntl.F_GETLK, lockdata
                )

                lockdata_list = struct.unpack(fmt, lockdata)
                pid = lockdata_list[pid_index]

                if pid > 0:
                    return pid

            except OSError:
                pass

            return None


# ==== helpers for daemon management =====================================================


def _sigterm_handler(signal_number: int, frame: FrameType) -> None:
    sys.exit()


def _send_term(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def maestral_lock(config_name: str) -> Lock:
    """
    Returns an inter-process and inter-thread lock for Maestral. This is a wrapper around
    :class:`Lock` which fills out the appropriate lockfile name and directory for the
    given config name.
    """
    name = f"{config_name}.lock"
    path = get_runtime_path("maestral")
    return Lock.singleton(name, path)


def sockpath_for_config(config_name: str) -> str:
    """
    Returns the unix socket location to be used for the config. This should default to
    the apps runtime directory + '/maestral/CONFIG_NAME.sock'.
    """
    return get_runtime_path("maestral", f"{config_name}.sock")


def lockpath_for_config(config_name: str) -> str:
    return get_runtime_path("maestral", f"{config_name}.lock")


def get_maestral_pid(config_name: str) -> Optional[int]:
    """
    Returns Maestral's PID if the daemon is running, ``None`` otherwise.

    :param config_name: The name of the Maestral configuration.
    :returns: The daemon's PID.
    """

    return maestral_lock(config_name).locking_pid()


def is_running(config_name: str) -> bool:
    """
    Checks if a daemon is currently running.

    :param config_name: The name of the Maestral configuration.
    :returns: Whether the daemon is running.
    """

    return maestral_lock(config_name).locked()


def _wait_for_startup(config_name: str, timeout: float = 8) -> Start:
    """Checks if we can communicate with the maestral daemon. Returns ``Start.Ok`` if
    communication succeeds within timeout, ``Start.Failed``  otherwise."""

    sock_name = sockpath_for_config(config_name)
    maestral_daemon = Proxy(URI.format(config_name, "./u:" + sock_name))

    while timeout > 0:
        try:
            maestral_daemon._pyroBind()
            return Start.Ok
        except Exception:
            time.sleep(0.2)
            timeout -= 0.2
        finally:
            maestral_daemon._pyroRelease()

    return Start.Failed


# ==== main functions to manage daemon ===================================================


async def _periodic_watchdog() -> None:

    if WATCHDOG_USEC:

        sleep = int(WATCHDOG_USEC)
        while True:
            sd_notifier.notify("WATCHDOG=1")
            await asyncio.sleep(sleep / (2 * 10 ** 6))


def start_maestral_daemon(
    config_name: str = "maestral", log_to_stdout: bool = False, start_sync: bool = False
) -> None:
    """
    Starts the Maestral daemon with event loop in the current thread. Startup is race
    free: there will never be two daemons running for the same config.

    Wraps :class:`main.Maestral` as Pyro daemon object, creates a new instance and starts
    an asyncio event loop to listen for requests on a unix domain socket. This call will
    block until the event loop shuts down. When this function is called from the main
    thread on macOS, the asyncio event loop uses Cocoa's CFRunLoop to process event. This
    allows integration with Cocoa frameworks which use callbacks to process use input such
    as clicked notifications, etc, and potentially allows showing a GUI.

    :param config_name: The name of the Maestral configuration to use.
    :param log_to_stdout: If ``True``, write logs to stdout.
    :param start_sync: If ``True``, start syncing once the daemon has started.
    :raises: :class:`RuntimeError` if a daemon for the given ``config_name`` is already
        running.
    """

    from maestral.main import Maestral

    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Must run daemon in main thread")

    # acquire PID lock file
    lock = maestral_lock(config_name)

    if not lock.acquire():
        raise RuntimeError("Maestral daemon is already running")

    # Nice ourselves to give other processes priority. We will likely only
    # have significant CPU usage in case of many concurrent downloads.
    os.nice(10)

    # catch sigterm and shut down gracefully
    signal.signal(signal.SIGTERM, _sigterm_handler)

    # integrate with CFRunLoop in macOS, only works in main thread
    if sys.platform == "darwin":

        from rubicon.objc.eventloop import EventLoopPolicy

        # clean up any pending tasks before we change the event loop policy
        # this is necessary if previous code has run an asyncio loop

        loop = asyncio.get_event_loop()
        pending_tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]

        for task in pending_tasks:
            task.cancel()

        loop.run_until_complete(asyncio.gather(*pending_tasks, return_exceptions=True))
        loop.close()

        # set new event loop policy
        asyncio.set_event_loop_policy(EventLoopPolicy())

    # get the default event loop
    loop = asyncio.get_event_loop()

    # notify systemd that we have started
    if NOTIFY_SOCKET:
        logger.debug("Running as systemd notify service")
        logger.debug("NOTIFY_SOCKET = %s", NOTIFY_SOCKET)
        sd_notifier.notify("READY=1")

    # notify systemd periodically if alive
    if IS_WATCHDOG and WATCHDOG_USEC:
        logger.debug("Running as systemd watchdog service")
        logger.debug("WATCHDOG_USEC = %s", WATCHDOG_USEC)
        logger.debug("WATCHDOG_PID = %s", WATCHDOG_PID)
        loop.create_task(_periodic_watchdog())

    # get socket for config name
    sockpath = sockpath_for_config(config_name)

    # clean up old socket
    try:
        os.remove(sockpath)
    except FileNotFoundError:
        pass

    # expose maestral as Pyro server
    # convert management methods to one way calls so that they don't block
    ExposedMaestral = expose(Maestral)

    ExposedMaestral.start_sync = oneway(ExposedMaestral.start_sync)
    ExposedMaestral.stop_sync = oneway(ExposedMaestral.stop_sync)
    ExposedMaestral.pause_sync = oneway(ExposedMaestral.pause_sync)
    ExposedMaestral.resume_sync = oneway(ExposedMaestral.resume_sync)
    ExposedMaestral.shutdown_daemon = oneway(ExposedMaestral.shutdown_daemon)

    maestral_daemon = ExposedMaestral(config_name, log_to_stdout=log_to_stdout)

    if start_sync:
        maestral_daemon.start_sync()

    try:

        with Daemon(unixsocket=sockpath) as daemon:
            daemon.register(maestral_daemon, f"maestral.{config_name}")

            for socket in daemon.sockets:
                loop.add_reader(socket.fileno(), daemon.events, daemon.sockets)

            loop.run_until_complete(maestral_daemon.shutdown_complete)

            for socket in daemon.sockets:
                loop.remove_reader(socket.fileno())

    except Exception:
        traceback.print_exc()
    finally:

        if NOTIFY_SOCKET:
            # notify systemd that we are shutting down
            sd_notifier.notify("STOPPING=1")

        sys.exit(0)


def _subprocess_launcher(
    config_name: str, log_to_stdout: bool, start_sync: bool
) -> None:
    """Launches a daemon subprocess with subprocess.Popen"""

    if IS_FROZEN:
        subprocess.Popen(
            [sys.executable, "--frozen-daemon", "-c", config_name],
            start_new_session=True,
        )
    else:
        # protect against injection
        cc = quote(config_name).strip("'")
        std_log = bool(log_to_stdout)
        start_sync = bool(start_sync)

        cmd = (
            f"import maestral.daemon; "
            f'maestral.daemon.start_maestral_daemon("{cc}", {std_log}, {start_sync})'
        )

        subprocess.Popen([sys.executable, "-c", cmd], start_new_session=True)


def start_maestral_daemon_process(
    config_name: str = "maestral",
    log_to_stdout: bool = False,
    start_sync: bool = False,
    detach: bool = True,
) -> Start:
    """
    Starts the Maestral daemon in a new process by calling :func:`start_maestral_daemon`.
    Startup is race free: there will never be two daemons running for the same config.

    This function assumes that ``sys.executable`` points to the Python executable or a
    frozen executable. In case of a frozen executable, the executable must take the
    command line argument '--frozen-daemon' to start a daemon process which is *not
    syncing* by calling :func:`start_maestral_daemon`. This is currently supported through
    :func:`freeze_support` which should be called from the main entry point, as soon as
    possible after startup.

    :param config_name: The name of the Maestral configuration to use.
    :param log_to_stdout: If ``True``, write logs to stdout.
    :param start_sync: If ``True``, start syncing once the daemon has started.
    :param detach: If ``True``, the daemon process will be detached. If ``False``,
        the daemon processes will run in the same session as the current process.
    :returns: ``Start.Ok`` if successful, ``Start.AlreadyRunning`` if the daemon was
        already running or ``Start.Failed`` if startup failed. It is possible that
        Start.Ok is returned instead of Start.AlreadyRunning in case of a race.
    """

    if is_running(config_name):
        return Start.AlreadyRunning

    if detach:
        _subprocess_launcher(config_name, log_to_stdout, start_sync)

    else:
        import multiprocessing as mp

        ctx = mp.get_context("spawn" if IS_MACOS else "fork")

        ctx.Process(
            target=start_maestral_daemon,
            args=(config_name, log_to_stdout, start_sync),
            name="maestral-daemon",
            daemon=True,
        ).start()

    return _wait_for_startup(config_name)


def stop_maestral_daemon_process(
    config_name: str = "maestral", timeout: float = 10
) -> Stop:
    """Stops a maestral daemon process by finding its PID and shutting it down.

    This function first tries to shut down Maestral gracefully. If this fails and we know
    its PID, it will send SIGTERM. If that fails as well, it will send SIGKILL to the
    process.

    :param config_name: The name of the Maestral configuration to use.
    :param timeout: Number of sec to wait for daemon to shut down before killing it.
    :returns: ``Stop.Ok`` if successful, ``Stop.Killed`` if killed, ``Stop.NotRunning`` if
        the daemon was not running and ``Exit.Failed`` if killing the process failed
        because we could not retrieve its PID.
    """

    if not is_running(config_name):
        return Stop.NotRunning

    pid = get_maestral_pid(config_name)

    try:
        with MaestralProxy(config_name) as m:
            m.shutdown_daemon()
    except CommunicationError:
        if pid:
            _send_term(pid)
    finally:
        while timeout > 0:
            if not is_running(config_name):
                return Stop.Ok
            else:
                time.sleep(0.2)
                timeout -= 0.2

        # send SIGTERM after timeout and delete PID file
        if pid:
            _send_term(pid)

        time.sleep(1)

        if not is_running(config_name):
            return Stop.Ok
        elif pid:
            os.kill(pid, signal.SIGKILL)
            return Stop.Killed
        else:
            return Stop.Failed


def get_maestral_proxy(
    config_name: str = "maestral", fallback: bool = False
) -> MaestralProxyType:
    """
    Returns a Pyro proxy of the a running Maestral instance.

    :param config_name: The name of the Maestral configuration to use.
    :param fallback: If ``True``, a new instance of Maestral will be returned when
        the daemon cannot be reached.
    :returns: Pyro proxy of Maestral or a new instance.
    :raises: :class:`Pyro5.errors.CommunicationError` if the daemon cannot be reached and
        ``fallback`` is ``False``.
    """

    if is_running(config_name):
        sock_name = sockpath_for_config(config_name)

        sys.excepthook = Pyro5.errors.excepthook
        maestral_daemon = Proxy(URI.format(config_name, "./u:" + sock_name))
        try:
            maestral_daemon._pyroBind()
            return maestral_daemon
        except CommunicationError:
            maestral_daemon._pyroRelease()

    if fallback:
        from maestral.main import Maestral

        return Maestral(config_name)
    else:
        raise CommunicationError("Could not get proxy")


class MaestralProxy:
    """
    A context manager to open and close a proxy to the Maestral daemon.

    :param config_name: The name of the Maestral configuration to use.
    :param fallback: If ``True``, a new instance of Maestral will be returned when
        the daemon cannot be reached.
    """

    def __init__(self, config_name: str = "maestral", fallback: bool = False) -> None:
        self.m = get_maestral_proxy(config_name, fallback)

    def __enter__(self) -> MaestralProxyType:
        return self.m

    def __exit__(
        self, exc_type: Type[Exception], exc_value: Exception, tb: TracebackType
    ):
        if isinstance(self.m, Proxy):
            self.m._pyroRelease()

        del self.m
