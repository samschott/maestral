"""
This module defines functions to start and stop the sync daemon and retrieve proxy
objects for a running daemon.
"""

from __future__ import annotations

# system imports
import inspect
import sys
import os
import time
import signal
import enum
import threading
import fcntl
import struct
import argparse
import re
import pickle
from pprint import pformat
from shlex import quote
from typing import Any, Iterable, ContextManager, TYPE_CHECKING
from types import TracebackType

# external imports
import Pyro5
from Pyro5.errors import CommunicationError
from Pyro5.api import (
    Daemon,
    Proxy,
    expose,
    register_dict_to_class,
    register_class_to_dict,
)
from Pyro5.serializers import serpent
from fasteners import InterProcessLock

# local imports
from .utils import exc_info_tuple
from .utils.appdirs import get_runtime_path
from .utils.integration import SystemdNotifier
from .constants import IS_MACOS, ENV
from . import core, models, exceptions


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
NOTIFY_SOCKET = os.getenv("NOTIFY_SOCKET")
WATCHDOG_PID = int(os.getenv("WATCHDOG_PID", os.getpid()))
WATCHDOG_USEC = os.getenv("WATCHDOG_USEC")
IS_WATCHDOG = WATCHDOG_USEC and WATCHDOG_PID == os.getpid()


URI = "PYRO:maestral.{0}@{1}"
Pyro5.config.THREADPOOL_SIZE_MIN = 2


def freeze_support() -> None:
    """
    Call this as early as possible in the main entry point of a frozen executable.
    This call will start the sync daemon if a matching command line arguments are
    detected and do nothing otherwise.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-c")
    parsed_args, _ = parser.parse_known_args()

    if parsed_args.c:
        template = r'.*start_maestral_daemon\("(?P<config_name>\S+)"\).*'
        match = re.match(template, parsed_args.c)

        if match:
            start_maestral_daemon(match["config_name"])
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


def check_signature(signature: str, obj: bytes) -> None:
    pass


def serialize_api_types(obj: Any) -> dict[str, Any]:
    """
    :param obj: Object to serialize.
    :returns: Serialized object.
    """
    res = pickle.dumps(obj)
    return {"__class__": type(obj).__name__, "object": res, "signature": ""}


def deserialize_api_types(class_name: str, d: dict[str, Any]) -> Any:
    """
    Deserializes an API type. Allowed classes are defined in:
        * :mod:`maestral.core`
        * :mod:`maestral.model`
        * :mod:`maestral.exceptions`

    :param class_name: Name of class to deserialize.
    :param d: Dictionary of serialized class.
    :returns: Deserialized object.
    """
    bytes_message = serpent.tobytes(d["object"])
    check_signature(d["signature"], bytes_message)
    return pickle.loads(bytes_message)


for module in core, models, exceptions:
    for klass_name, klass in inspect.getmembers(module, inspect.isclass):
        register_class_to_dict(klass, serialize_api_types)
        register_dict_to_class(klass_name, deserialize_api_types)


# ==== interprocess locking ============================================================


class Lock:
    """An inter-process and inter-thread lock

    This internally uses :class:`fasteners.InterProcessLock` but provides non-blocking
    acquire. It also guarantees thread-safety when using the :meth:`singleton` class
    method to create / retrieve a lock instance.

    :param path: Path of the lock file to use / create.
    """

    _instances: dict[str, Lock] = {}
    _singleton_lock = threading.Lock()

    @classmethod
    def singleton(cls, path: str) -> Lock:
        """
        Retrieve an existing lock object with a given 'name' or create a new one. Use
        this method for thread-safe locks.

        :param path: Path of the lock file to use / create.
        """
        with cls._singleton_lock:
            path = os.path.abspath(path)

            if path not in cls._instances:
                cls._instances[path] = cls(path)

            return cls._instances[path]

    def __init__(self, path: str) -> None:
        self.path = path
        self.pid = os.getpid()
        self._external_lock = InterProcessLock(self.path)
        self._lock = threading.RLock()

    def acquire(self) -> bool:
        """
        Attempts to acquire the given lock.

        :returns: Whether the acquisition succeeded.
        """
        with self._lock:
            if self._external_lock.acquired:
                return False
            return self._external_lock.acquire(blocking=False)

    def release(self) -> None:
        """Release the previously acquired lock."""
        with self._lock:
            if not self._external_lock.acquired:
                raise RuntimeError(
                    "Cannot release a lock, it was acquired by a different process"
                )

            self._external_lock.release()

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

    def locking_pid(self) -> int | None:
        """
        Returns the PID of the process which currently holds the lock or ``None``. This
        should work on macOS, OpenBSD and Linux but may fail on some platforms. Always
        use :meth:`locked` to check if the lock is held by any process.

        :returns: The PID of the process which currently holds the lock or ``None``.
        """
        with self._lock:
            if self._external_lock.acquired:
                return self.pid

            try:
                fh = open(self._external_lock.path, "a")
            except OSError:
                return None

            if IS_MACOS:
                fmt = "qqihh"
                pid_index = 2
                flock = struct.pack(fmt, 0, 0, 0, fcntl.F_WRLCK, 0)
            else:
                fmt = "hhqqih"
                pid_index = 4
                flock = struct.pack(fmt, fcntl.F_WRLCK, 0, 0, 0, 0, 0)

            lockdata = fcntl.fcntl(fh.fileno(), fcntl.F_GETLK, flock)
            lockdata_list = struct.unpack(fmt, lockdata)
            pid = lockdata_list[pid_index]

            if pid > 0:
                return pid

            return None


# ==== helpers for daemon management ===================================================


def _send_signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def maestral_lock(config_name: str) -> Lock:
    """
    Returns an inter-process and inter-thread lock for Maestral. This is a wrapper
    around :class:`Lock` which fills out the appropriate lockfile path for the given
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


def get_maestral_pid(config_name: str) -> int | None:
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


def wait_for_startup(config_name: str, timeout: float = 30) -> None:
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
    config_name: str = "maestral", log_to_stderr: bool = False
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
    :raises RuntimeError: if a daemon for the given ``config_name`` is already running.
    """

    import asyncio
    from .main import Maestral
    from .logging import scoped_logger, setup_logging

    setup_logging(config_name, stderr=log_to_stderr)
    dlogger = scoped_logger(__name__, config_name)
    sd_notifier = SystemdNotifier()

    dlogger.info("Starting daemon")

    # ==== Process and thread management ===========================================

    if threading.current_thread() is not threading.main_thread():
        dlogger.error("Must run daemon in main thread")
        return

    dlogger.debug("Environment:\n%s", pformat(os.environ.copy()))

    # Acquire PID lock file.
    lock = maestral_lock(config_name)

    if lock.acquire():
        dlogger.debug("Acquired daemon lock: %r", lock.path)
    else:
        dlogger.error("Could not acquire lock, daemon is already running")
        return

    try:
        # Nice ourselves to give other processes priority.
        os.nice(10)

        # ==== System integration ======================================================
        # Integrate with CFRunLoop in macOS.
        event_loop_policy: asyncio.AbstractEventLoopPolicy
        if IS_MACOS:
            dlogger.debug("Integrating with CFEventLoop")

            from rubicon.objc.eventloop import EventLoopPolicy

            event_loop_policy = EventLoopPolicy()

        else:
            event_loop_policy = asyncio.get_event_loop_policy()

        # Get the default event loop.
        loop = event_loop_policy.new_event_loop()

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
                        await asyncio.sleep(sleep / (2 * 10**6))

            dlogger.debug("Running as systemd watchdog service")
            dlogger.debug("WATCHDOG_USEC = %s", WATCHDOG_USEC)
            dlogger.debug("WATCHDOG_PID = %s", WATCHDOG_PID)
            loop.create_task(periodic_watchdog())

        # ==== Run Maestral as Pyro server =============================================
        # Get socket for config name.
        sockpath = sockpath_for_config(config_name)
        dlogger.debug("Socket path: %r", sockpath)

        # Clean up old socket.
        try:
            os.remove(sockpath)
        except (FileNotFoundError, NotADirectoryError):
            pass

        # Expose maestral as Pyro server.
        dlogger.debug("Creating Pyro daemon")

        shutdown_future = loop.create_future()
        maestral_daemon = expose(Maestral)(
            config_name,
            log_to_stderr=log_to_stderr,
            event_loop=loop,
            shutdown_future=shutdown_future,
        )

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

            loop.run_until_complete(shutdown_future)

            for socket in daemon.sockets:
                loop.remove_reader(socket.fileno())

            # Prevent Pyro housekeeping from blocking shutdown.
            daemon.transportServer.housekeeper = None

    except Exception as exc:
        dlogger.error(exc.args[0], exc_info=True)
    finally:
        # Notify systemd that we are shutting down.
        sd_notifier.notify("STOPPING=1")

        lock.release()


def start_maestral_daemon_process(
    config_name: str = "maestral", timeout: float = 30
) -> Start:
    """
    Starts the Maestral daemon in a new process by calling :func:`start_maestral_daemon`.

    Startup is race free: there will never be more than one daemon running for the same
    config name. This function will use :obj:`sys.executable` as a Python executable to
    start the daemon.

    Environment variables from the current process will be preserved and updated with
    the environment variables defined in :const:`constants.ENV`.

    :param config_name: The name of the Maestral configuration to use.
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

    script = f'import maestral.daemon; maestral.daemon.start_maestral_daemon("{cc}")'

    env = os.environ.copy()
    env.update(ENV)

    pid = os.spawnve(os.P_NOWAIT, sys.executable, [sys.executable, "-c", script], env)

    try:
        wait_for_startup(config_name, timeout)
    except Exception as exc:
        from .logging import scoped_logger, setup_logging

        setup_logging(config_name, stderr=False)
        clogger = scoped_logger(__name__, config_name)

        clogger.error("Could not communicate with daemon", exc_info=exc_info_tuple(exc))

        # Let's check if the daemon is running
        try:
            os.kill(pid, 0)
            clogger.error("Daemon is running but not responsive, killing now")
        except OSError:
            clogger.error("Daemon quit unexpectedly")
        else:
            os.kill(pid, signal.SIGTERM)

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

    if not pid:
        # Cannot send SIGTERM to process if we don't know its pid.
        return Stop.Failed

    _send_signal(pid, signal.SIGTERM)

    while timeout > 0:
        if not is_running(config_name):
            return Stop.Ok
        else:
            time.sleep(0.2)
            timeout -= 0.2

    # Kill.
    _send_signal(pid, signal.SIGKILL)
    return Stop.Killed


class MaestralProxy(ContextManager["MaestralProxy"]):
    """A Proxy to the Maestral daemon

    All methods and properties of Maestral's public API are accessible and calls /
    access will be forwarded to the corresponding Maestral instance. This class can be
    used as a context manager to close the connection to the daemon on exit.

    :Example:

        Use MaestralProxy as a context manager:

        >>> import src.maestral.cli.cli_info
        >>> with MaestralProxy() as m:
        ...     print(src.maestral.cli.cli_info.status)

        Use MaestralProxy directly:

        >>> import src.maestral.cli.cli_info
        >>> m = MaestralProxy()
        >>> print(src.maestral.cli.cli_info.status)
        >>> m._disconnect()

    :ivar _is_fallback: Whether we are using an actual Maestral instance as fallback
        instead of a Proxy.

    :param config_name: The name of the Maestral configuration to use.
    :param fallback: If ``True``, a new instance of Maestral will be created in the
        current process when the daemon is not running.
    :raises CommunicationError: if the daemon is running but cannot be reached or if the
        daemon is not running and ``fallback`` is ``False``.
    """

    _m: Maestral | Proxy

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

        elif fallback:
            from .main import Maestral

            self._m = Maestral(config_name)
        else:
            raise CommunicationError(f"Could not get proxy for '{config_name}'")

        self._is_fallback = not isinstance(self._m, Proxy)

    def _disconnect(self) -> None:
        if isinstance(self._m, Proxy):
            self._m._pyroRelease()

    def __enter__(self) -> MaestralProxy:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        tb: TracebackType | None,
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

    def __setattr__(self, key: str, value: Any) -> None:
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
