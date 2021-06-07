# -*- coding: utf-8 -*-
"""
This module defines functions to start and stop the sync daemon and retrieve proxy
objects for a running daemon.
"""

# system imports
import sys
import os
import time
import signal
import enum
import subprocess
import threading
import fcntl
import struct
import warnings
import argparse
import ast
import re
from pprint import pformat
from shlex import quote
from typing import Optional, Any, Union, Tuple, Dict, Iterable, Type, TYPE_CHECKING
from types import TracebackType

# external imports
import Pyro5  # type: ignore
from Pyro5.errors import CommunicationError  # type: ignore
from Pyro5.api import Daemon, Proxy, expose, oneway, register_dict_to_class  # type: ignore
import sdnotify  # type: ignore
from fasteners import InterProcessLock  # type: ignore

# local imports
from .errors import SYNC_ERRORS, GENERAL_ERRORS, MaestralApiError
from .utils import exc_info_tuple
from .utils.appdirs import get_runtime_path
from .constants import IS_MACOS, ENV


if TYPE_CHECKING:
    from .main import Maestral


__all__ = [
    "Stop",
    "Start",
    "Lock",
    "maestral_lock",
    "get_maestral_pid",
    "sockpath_for_config",
    "lockpath_for_config",
    "wait_for_startup",
    "is_running",
    "freeze_support",
    "start_maestral_daemon",
    "start_maestral_daemon_process",
    "stop_maestral_daemon_process",
    "MaestralProxy",
    "CommunicationError",
]


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


def freeze_support() -> None:
    """
    Call this as early as possible in the main entry point of a frozen executable.
    This call will start the sync daemon if a matching command line arguments are
    detected and do nothing otherwise.
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("-c")
    parsed_args, _ = parser.parse_known_args()

    if parsed_args.c:
        template = r'.*start_maestral_daemon\("(?P<config_name>\S+)", start_sync=(?P<start_sync>\S+)\).*'
        match = re.match(template, parsed_args.c)

        if match:
            config_name = match["config_name"]
            start_sync = ast.literal_eval(match["start_sync"])

            start_maestral_daemon(config_name, start_sync)
            sys.exit()


class Stop(enum.Enum):
    """Enumeration of daemon exit results"""

    Ok = 0
    Killed = 1
    NotRunning = 2
    Failed = 3


class Start(enum.Enum):
    """Enumeration of daemon start results"""

    Ok = 0
    AlreadyRunning = 1
    Failed = 2


# ==== error serialization =============================================================


def serpent_deserialize_api_error(class_name: str, d: dict) -> MaestralApiError:
    """
    Deserializes a MaestralApiError.

    :param class_name: Name of class to deserialize.
    :param d: Dictionary of serialized class.
    :returns: Class instance.
    """

    # Import maestral.errors for evaluation.
    # This import needs to be absolute to reconstruct the Exception class. Note that the
    # eval is safe here because ``serpent_deserialize_api_error`` is only registered for
    # strings that match an error class name.

    import maestral.errors  # noqa: F401

    cls = eval(class_name)
    err = cls(*d["args"])
    for a_name, a_value in d["attributes"].items():
        setattr(err, a_name, a_value)

    return err


for err_cls in (*SYNC_ERRORS, *GENERAL_ERRORS):
    register_dict_to_class(
        err_cls.__module__ + "." + err_cls.__name__, serpent_deserialize_api_error
    )


# ==== interprocess locking ============================================================


def _get_lockdata() -> Tuple[bytes, str, int]:

    try:
        os.O_LARGEFILE
    except AttributeError:
        start_len = "ll"
    else:
        start_len = "qq"

    if sys.platform.startswith(("netbsd", "freebsd", "openbsd")) or IS_MACOS:
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
    """A inter-process and inter-thread lock

    This internally uses :class:`fasteners.InterProcessLock` but provides non-blocking
    acquire. It also guarantees thread-safety when using the :meth:`singleton` class
    method to create / retrieve a lock instance.

    :param path: Path of the lock file to use / create.
    """

    _instances: Dict[str, "Lock"] = {}
    _singleton_lock = threading.Lock()

    @classmethod
    def singleton(cls, path: str) -> "Lock":
        """
        Retrieve an existing lock object with a given 'name' or create a new one. Use
        this method for thread-safe locks.

        :param path: Path of the lock file to use / create.
        """

        with cls._singleton_lock:
            try:
                instance = cls._instances[path]
            except KeyError:
                instance = cls(path)
                cls._instances[path] = instance

            return instance

    def __init__(self, path: str) -> None:

        self.path = path

        self._internal_lock = threading.Semaphore()
        self._external_lock = InterProcessLock(self.path)

        self._lock = threading.RLock()
        self._acquired = False  # tracks if we acquired the lock

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
                    self._acquired = True
                    return True
                else:
                    self._internal_lock.release()
                    return False

    def release(self) -> None:
        """Release the previously acquired lock."""
        with self._lock:

            if not self._acquired:
                raise RuntimeError(
                    "Cannot release a lock, it was acquired by a different process"
                )

            self._external_lock.release()
            self._internal_lock.release()
            self._acquired = False

    def locked(self) -> bool:
        """
        Checks if the lock is currently held by any thread or process.

        :returns: Whether the lock is acquired.
        """
        with self._lock:
            gotten = self.acquire()
            if gotten:
                self.release()
            return not gotten

    def locking_pid(self) -> Optional[int]:
        """
        Returns the PID of the process which currently holds the lock or ``None``. This
        should work on macOS, OpenBSD and Linux but may fail on some platforms. Always
        use :meth:`locked` to check if the lock is held by any process.

        :returns: The PID of the process which currently holds the lock or ``None``.
        """

        with self._lock:

            if self._acquired:
                return os.getpid()

            try:
                # Don't close again in case we are the locking process.
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


# ==== helpers for daemon management ===================================================


def _send_term(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def maestral_lock(config_name: str) -> Lock:
    """
    Returns an inter-process and inter-thread lock for Maestral. This is a wrapper
    around :class:`Lock` which fills out the appropriate lockfile path for he given
    config name.

    :param config_name: The name of the Maestral configuration.
    :returns: Lock instance for the config name
    """
    name = f"{config_name}.lock"
    path = get_runtime_path("maestral")
    return Lock.singleton(os.path.join(path, name))


def sockpath_for_config(config_name: str) -> str:
    """
    Returns the unix socket location to be used for the config. This should default to
    the apps runtime directory + 'CONFIG_NAME.sock'.

    :param config_name: The name of the Maestral configuration.
    :returns: Socket path.
    """
    return get_runtime_path("maestral", f"{config_name}.sock")


def lockpath_for_config(config_name: str) -> str:
    """
    Returns the lock file location to be used for the config. This will be the apps
    runtime directory + 'CONFIG_NAME.lock'.

    :param config_name: The name of the Maestral configuration.
    :returns: Path of lock file to use.
    """
    return get_runtime_path("maestral", f"{config_name}.lock")


def get_maestral_pid(config_name: str) -> Optional[int]:
    """
    Returns the PID of the daemon if it is running, ``None`` otherwise.

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


def wait_for_startup(config_name: str, timeout: float = 5) -> None:
    """
    Waits until we can communicate with the maestral daemon for ``config_name``.

    :param config_name: Configuration to connect to.
    :param timeout: Timeout it seconds until we raise an error.
    :raises CommunicationError: if we cannot communicate with the daemon within the
        given timeout.
    """

    sock_name = sockpath_for_config(config_name)
    maestral_daemon = Proxy(URI.format(config_name, "./u:" + sock_name))

    t0 = time.time()

    while True:
        try:
            maestral_daemon._pyroBind()
            return
        except Exception as exc:
            if time.time() - t0 > timeout:
                raise exc
            else:
                time.sleep(0.2)
        finally:
            maestral_daemon._pyroRelease()


# ==== main functions to manage daemon =================================================


def start_maestral_daemon(
    config_name: str = "maestral", log_to_stderr: bool = False, start_sync: bool = False
) -> None:
    """
    Starts the Maestral daemon with event loop in the current thread.

    Startup is race free: there will never be more than one daemon running with the same
    config name. The daemon is a :class:`maestral.main.Maestral` instance which is
    exposed as Pyro daemon object and listens for requests on a unix domain socket. This
    call starts an asyncio event loop to process client requests and blocks until the
    event loop shuts down. On macOS, the event loop is integrated with Cocoa's
    CFRunLoop. This allows processing Cocoa events and callbacks, for instance for
    desktop notifications.

    :param config_name: The name of the Maestral configuration to use.
    :param log_to_stderr: If ``True``, write logs to stderr.
    :param start_sync: If ``True``, start syncing once the daemon has started. If the
        ``start_sync`` call fails, an error will be logged but not raised.
    :raises RuntimeError: if a daemon for the given ``config_name`` is already running.
    """

    import asyncio
    from .main import Maestral
    from .logging import scoped_logger, setup_logging

    setup_logging(config_name, log_to_stderr)
    dlogger = scoped_logger(__name__, config_name)

    dlogger.info("Starting daemon")

    if threading.current_thread() is not threading.main_thread():
        dlogger.error("Must run daemon in main thread")
        raise RuntimeError("Must run daemon in main thread")

    dlogger.debug("Environment:\n%s", pformat(os.environ.copy()))

    # acquire PID lock file
    lock = maestral_lock(config_name)

    if lock.acquire():
        dlogger.debug("Acquired daemon lock: %s", lock.path)
    else:
        dlogger.error("Could not acquire lock, daemon is already running")
        raise RuntimeError("Daemon is already running")

    # Nice ourselves to give other processes priority.
    os.nice(10)

    # Integrate with CFRunLoop in macOS.
    if IS_MACOS:

        dlogger.debug("Integrating with CFEventLoop")

        from rubicon.objc.eventloop import EventLoopPolicy  # type: ignore

        asyncio.set_event_loop_policy(EventLoopPolicy())

    # Get the default event loop.
    loop = asyncio.get_event_loop()

    sd_notifier = sdnotify.SystemdNotifier()

    # Notify systemd that we have started.
    if NOTIFY_SOCKET:
        dlogger.debug("Running as systemd notify service")
        dlogger.debug("NOTIFY_SOCKET = %s", NOTIFY_SOCKET)
        sd_notifier.notify("READY=1")

    # Notify systemd periodically if alive.
    if IS_WATCHDOG and WATCHDOG_USEC:

        async def periodic_watchdog() -> None:

            if WATCHDOG_USEC:

                sleep = int(WATCHDOG_USEC)
                while True:
                    sd_notifier.notify("WATCHDOG=1")
                    await asyncio.sleep(sleep / (2 * 10 ** 6))

        dlogger.debug("Running as systemd watchdog service")
        dlogger.debug("WATCHDOG_USEC = %s", WATCHDOG_USEC)
        dlogger.debug("WATCHDOG_PID = %s", WATCHDOG_PID)
        loop.create_task(periodic_watchdog())

    # Get socket for config name.
    sockpath = sockpath_for_config(config_name)
    dlogger.debug(f"Socket path: '{sockpath}'")

    # Clean up old socket.
    try:
        os.remove(sockpath)
    except FileNotFoundError:
        pass

    # Expose maestral as Pyro server. Convert management
    # methods to one way calls so that they don't block.

    dlogger.debug("Creating Pyro daemon")

    ExposedMaestral = expose(Maestral)

    ExposedMaestral.start_sync = oneway(ExposedMaestral.start_sync)
    ExposedMaestral.stop_sync = oneway(ExposedMaestral.stop_sync)
    ExposedMaestral.shutdown_daemon = oneway(ExposedMaestral.shutdown_daemon)

    maestral_daemon = ExposedMaestral(config_name, log_to_stderr=log_to_stderr)

    if start_sync:
        dlogger.debug("Starting sync")
        maestral_daemon.start_sync()

    try:

        dlogger.debug("Starting event loop")

        with Daemon(unixsocket=sockpath) as daemon:
            daemon.register(maestral_daemon, f"maestral.{config_name}")

            # Reduce Pyro's housekeeping frequency from 2 sec to 20 sec.
            # This avoids constantly waking the CPU when we are idle.
            if daemon.transportServer.housekeeper:
                daemon.transportServer.housekeeper.waittime = 20

            for socket in daemon.sockets:
                loop.add_reader(socket.fileno(), daemon.events, daemon.sockets)

            # Handle sigterm gracefully.
            signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
            for s in signals:
                loop.add_signal_handler(s, maestral_daemon.shutdown_daemon)

            loop.run_until_complete(maestral_daemon.shutdown_complete)

            for socket in daemon.sockets:
                loop.remove_reader(socket.fileno())

            # Prevent Pyro housekeeping from blocking shutdown.
            daemon.transportServer.housekeeper = None

    except Exception as exc:
        dlogger.error(exc.args[0], exc_info=True)
    finally:

        if NOTIFY_SOCKET:
            # Notify systemd that we are shutting down.
            sd_notifier.notify("STOPPING=1")


def start_maestral_daemon_process(
    config_name: str = "maestral",
    start_sync: bool = False,
    timeout: int = 5,
) -> Start:
    """
    Starts the Maestral daemon in a new process by calling :func:`start_maestral_daemon`.

    Startup is race free: there will never be more than one daemon running for the same
    config name. This function will use :obj:`sys.executable` as a Python executable to
    start the daemon.

    Environment variables from the current process will be preserved and updated with
    the environment variables defined in :const:`constants.ENV`.

    :param config_name: The name of the Maestral configuration to use.
    :param start_sync: If ``True``, start syncing once the daemon has started.
    :param timeout: Time in sec to wait for daemon to start.
    :returns: :attr:`Start.Ok` if successful, :attr:`Start.AlreadyRunning` if the daemon
        was already running or :attr:`Start.Failed` if startup failed. It is possible
        that :attr:`Start.Ok` may be returned instead of :attr:`Start.AlreadyRunning`
        in case of a race but the daemon is nevertheless started only once.
    """

    if is_running(config_name):
        return Start.AlreadyRunning

    # Protect against injection.
    cc = quote(config_name).strip("'")
    start_sync = bool(start_sync)

    script = (
        f"import maestral.daemon; "
        f'maestral.daemon.start_maestral_daemon("{cc}", start_sync={start_sync})'
    )

    cmd = [sys.executable, "-c", script]

    subprocess_env = os.environ.copy()
    subprocess_env.update(ENV)

    process = subprocess.Popen(
        cmd,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=subprocess_env,
    )

    try:
        wait_for_startup(config_name, timeout)
    except Exception as exc:
        from .logging import scoped_logger, setup_logging

        setup_logging(config_name, log_to_stderr=False)
        clogger = scoped_logger(__name__, config_name)

        clogger.error("Could not communicate with daemon", exc_info=exc_info_tuple(exc))

        # Let's check what the daemon has been doing.
        returncode = process.poll()
        if returncode is None:
            clogger.error("Daemon is running but not responsive, killing now")
            process.terminate()
        else:
            clogger.error("Daemon stopped with return code %s", returncode)
        return Start.Failed
    else:
        return Start.Ok


def stop_maestral_daemon_process(
    config_name: str = "maestral", timeout: float = 10
) -> Stop:
    """Stops a maestral daemon process by finding its PID and shutting it down.

    This function first tries to shut down Maestral gracefully. If this fails and we
    know its PID, it will send SIGTERM. If that fails as well, it will send SIGKILL to
    the process.

    :param config_name: The name of the Maestral configuration to use.
    :param timeout: Number of sec to wait for daemon to shut down before killing it.
    :returns: :attr:`Stop.Ok` if successful, :attr:`Stop.Killed` if killed,
        :attr:`Stop.NotRunning` if the daemon was not running and :attr:`Stop.Failed`
        if killing the process failed because we could not retrieve its PID.
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

        # Send SIGTERM after timeout and delete PID file.
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


class MaestralProxy:
    """A Proxy to the Maestral daemon

    All methods and properties of Maestral's public API are accessible and calls /
    access will be forwarded to the corresponding Maestral instance. This class can be
    used as a context manager to close the connection to the daemon on exit.

    :Example:

        Use MaestralProxy as a context manager:

        >>> with MaestralProxy() as m:
        ...     print(m.status)

        Use MaestralProxy directly:

        >>> m = MaestralProxy()
        >>> print(m.status)
        >>> m._disconnect()

    :ivar _is_fallback: Whether we are using an actual Maestral instance as fallback
        instead of a Proxy.

    :param config_name: The name of the Maestral configuration to use.
    :param fallback: If ``True``, a new instance of Maestral will created in the current
        process when the daemon is not running.
    :raises CommunicationError: if the daemon is running but cannot be reached or if the
        daemon is not running and ``fallback`` is ``False``.
    """

    _m: Union["Maestral", Proxy]

    def __init__(self, config_name: str = "maestral", fallback: bool = False) -> None:

        self._config_name = config_name
        self._is_fallback = False

        if is_running(config_name):

            sock_name = sockpath_for_config(config_name)

            # print remote tracebacks locally
            sys.excepthook = Pyro5.errors.excepthook

            self._m = Proxy(URI.format(config_name, "./u:" + sock_name))
            try:
                self._m._pyroBind()
            except CommunicationError:
                self._m._pyroRelease()
                raise

        else:
            # If daemon is not running, fall back to new Maestral instance
            # or raise a CommunicationError if fallback not allowed.
            if fallback:
                from .main import Maestral

                self._m = Maestral(config_name)
            else:
                raise CommunicationError(f"Could not get proxy for '{config_name}'")

        self._is_fallback = not isinstance(self._m, Proxy)

    def _disconnect(self) -> None:
        if isinstance(self._m, Proxy):
            self._m._pyroRelease()

    def __enter__(self) -> "MaestralProxy":
        return self

    def __exit__(
        self, exc_type: Type[Exception], exc_value: Exception, tb: TracebackType
    ) -> None:
        self._disconnect()
        del self._m

    def __getattr__(self, item: str) -> Any:
        if item.startswith("_"):
            super().__getattribute__(item)
        elif isinstance(self._m, Proxy):
            return self._m.__getattr__(item)
        else:
            return self._m.__getattribute__(item)

    def __setattr__(self, key, value) -> None:
        if key.startswith("_"):
            super().__setattr__(key, value)
        else:
            self._m.__setattr__(key, value)

    def __dir__(self) -> Iterable[str]:
        own_result = dir(self.__class__) + list(self.__dict__.keys())
        proxy_result = [k for k in self._m.__dir__() if not k.startswith("_")]

        return sorted(set(own_result) | set(proxy_result))

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(config={self._config_name!r}, "
            f"is_fallback={self._is_fallback})>"
        )


def get_maestral_proxy(
    config_name: str = "maestral", fallback: bool = False
) -> Union["Maestral", Proxy]:

    warnings.warn(
        "'get_maestral_proxy' is deprecated, please use 'MaestralProxy' instead",
        DeprecationWarning,
    )

    m = MaestralProxy(config_name, fallback=fallback)
    return m._m
