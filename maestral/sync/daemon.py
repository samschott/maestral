# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

"""
# system imports
import sys
import os
import time
import logging
import signal
import traceback
import enum

# external packages
import Pyro5.errors
from Pyro5.api import Daemon, Proxy, expose
from Pyro5.serializers import SerpentSerializer
from lockfile.pidlockfile import PIDLockFile, AlreadyLocked

# internal modules
from maestral.sync.errors import MaestralApiError, SYNC_ERRORS, FATAL_ERRORS


logger = logging.getLogger(__name__)
URI = "PYRO:maestral.{0}@{1}"


class Exit(enum.Enum):
    Ok = 0
    Killed = 1
    NotRunning = 2

class Start(enum.Enum):
    Ok = 0
    Failed = 1
    AlreadyRunning = 2


# ==== error serialization ===============================================================

def serpent_deserialize_api_error(class_name, d):
    import maestral.sync.errors
    cls = eval(class_name)
    e = cls(*d['args'])
    for a_name, a_value in d['attributes'].items():
        setattr(e, a_name, a_value)

    return e


for err_cls in list(SYNC_ERRORS) + list(FATAL_ERRORS) + [MaestralApiError]:
    SerpentSerializer.register_dict_to_class(
        err_cls.__module__ + "." + err_cls.__name__,
        serpent_deserialize_api_error
    )


# ==== helpers for daemon management =====================================================

def sockpath_for_config(config_name):
    """
    Returns the unix socket location to be used for the config. This should default to
    the apps runtime directory + '/maestral/CONFIG_NAME.sock'.
    """
    from maestral.sync.utils.appdirs import get_runtime_path
    return get_runtime_path("maestral", config_name + ".sock")


def pidpath_for_config(config_name):
    from maestral.sync.utils.appdirs import get_runtime_path
    return get_runtime_path("maestral", config_name + ".pid")


def is_pidfile_stale(pidfile):
    """
    Determine whether a PID file is stale. Returns ``True`` if the PID file is stale,
    ``False`` otherwise. The PID file is “stale” if its contents are valid but do not
    match the PID of a currently-running process.
    """
    result = False

    pidfile_pid = pidfile.read_pid()
    if pidfile_pid is not None:
        try:
            os.kill(pidfile_pid, signal.SIG_DFL)
        except ProcessLookupError:
            # The specified PID does not exist.
            result = True

    return result


def get_maestral_pid(config_name):
    """
    Returns Maestral's PID if the daemon is running and responsive, ``None``
    otherwise. If the daemon is unresponsive, it will be killed before returning.

    :param str config_name: The name of the Maestral configuration to use.
    :returns: The daemon's PID.
    :rtype: int
    """

    lockfile = PIDLockFile(pidpath_for_config(config_name))
    pid = lockfile.read_pid()

    if pid:
        try:
            if not is_pidfile_stale(lockfile):
                return pid
        except OSError:
            os.kill(pid, signal.SIGKILL)
            logger.debug(f"Daemon process with PID {pid} is not responsive. Killed.")
    else:
        logger.debug("Could not find PID file")

    lockfile.break_lock()


def _wait_for_startup(config_name, timeout=8):
    """Waits for the daemon to start and verifies Pyro communication. Returns ``Start.Ok``
    if startup and communication succeeds within timeout, ``Start.Failed`` otherwise."""
    t0 = time.time()
    pid = None

    while not pid and time.time() - t0 < timeout/2:
        pid = get_maestral_pid(config_name)

    if pid:
        return _check_pyro_communication(config_name, timeout=int(timeout/2))
    else:
        return Start.Failed


def _check_pyro_communication(config_name, timeout=2):
    """Checks if we can communicate with the maestral daemon. Returns ``Start.Ok`` if
    communication succeeds within timeout, ``Start.Failed``  otherwise."""

    sock_name = sockpath_for_config(config_name)
    maestral_daemon = Proxy(URI.format(config_name, "./u:" + sock_name))

    # wait until we can communicate with daemon, timeout after :param:`timeout`
    while timeout > 0:
        try:
            maestral_daemon._pyroBind()
            logger.debug("Successfully communication with daemon")
            return Start.Ok
        except Exception:
            time.sleep(0.2)
            timeout -= 0.2
        finally:
            maestral_daemon._pyroRelease()

    logger.error("Could communicate with Maestral daemon")
    return Start.Failed


# ==== main functions to manage daemon ===================================================

def run_maestral_daemon(config_name="maestral", run=True, log_to_stdout=False):
    """
    Wraps :class:`maestral.main.Maestral` as Pyro daemon object, creates a new instance
    and start Pyro's event loop to listen for requests on a unix domain socket. This call
    will block until the event loop shuts down.

    This command will return silently if the daemon is already running.

    :param str config_name: The name of the Maestral configuration to use.
    :param bool run: If ``True``, start syncing automatically. Defaults to ``True``.
    :param bool log_to_stdout: If ``True``, write logs to stdout. Defaults to ``False``.
    """

    from maestral.sync.main import Maestral

    sock_name = sockpath_for_config(config_name)
    pid_name = pidpath_for_config(config_name)

    lockfile = PIDLockFile(pid_name)

    # acquire PID lock file

    try:
        lockfile.acquire(timeout=1)
    except AlreadyLocked:
        if is_pidfile_stale(lockfile):
            lockfile.break_lock()
        else:
            logger.debug(f"Maestral already running")
            return

    logger.debug(f"Starting Maestral daemon on socket '{sock_name}'")

    try:
        # clean up old socket, create new one
        try:
            os.remove(sock_name)
        except FileNotFoundError:
            pass

        daemon = Daemon(unixsocket=sock_name)

        # start Maestral as Pyro server
        ExposedMaestral = expose(Maestral)
        m = ExposedMaestral(config_name, run=run)
        m.set_log_to_stdout(log_to_stdout)

        daemon.register(m, f"maestral.{config_name}")
        daemon.requestLoop(loopCondition=m._loop_condition)
        daemon.close()
    except Exception:
        traceback.print_exc()
    finally:
        # remove PID lock
        lockfile.release()


def start_maestral_daemon_thread(config_name="maestral", run=True):
    """
    Starts the Maestral daemon in a thread (by calling `start_maestral_daemon`).
    This command will create a new daemon on each run. Take care not to sync the same
    directory with multiple instances of Meastral! You can use `get_maestral_process_info`
    to check if either a Meastral gui or daemon is already running for the given
    `config_name`.

    :param str config_name: The name of the Maestral configuration to use.
    :param bool run: If ``True``, start syncing automatically. Defaults to ``True``.
    :returns: ``True`` if started, ``False`` otherwise.
    :rtype: bool
    """
    import threading

    threading.Thread(
        target=run_maestral_daemon,
        args=(config_name, run),
        name="Maestral daemon",
        daemon=True,
    ).start()

    return _wait_for_startup(config_name, timeout=6)


def start_maestral_daemon_process(config_name="maestral", run=True):
    """
    Starts the Maestral daemon as a separate process (by calling `start_maestral_daemon`).

    :param str config_name: The name of the Maestral configuration to use.
    :param bool run: If ``True``, start syncing automatically. Defaults to ``True``.
    :returns: ``Start.Ok`` if successful, ``Start.Failed`` otherwise.
    """
    import subprocess
    from shlex import quote
    import multiprocessing as mp

    STD_IN_OUT = subprocess.DEVNULL

    # use nested Popen and multiprocessing.Process to effectively create double fork
    # see Unix "double-fork magic"

    def target(cc, r):
        cc = quote(cc)
        r = bool(r)
        subprocess.Popen(
            [sys.executable, "-c", f"from maestral.sync.daemon import run_maestral_daemon; run_maestral_daemon('{cc}', {r})"],
            stdin=STD_IN_OUT, stdout=STD_IN_OUT, stderr=STD_IN_OUT,
        )

    mp.Process(
        target=target,
        args=(config_name, run),
        name="Maestral daemon launcher",
        daemon=True,
    ).start()

    return _wait_for_startup(config_name, timeout=6)


def stop_maestral_daemon_process(config_name="maestral", timeout=10):
    """Stops maestral by finding its PID and shutting it down.

    This function first tries to shut down Maestral gracefully. If this fails, it will
    send SIGTERM. If that fails as well, it will send SIGKILL.

    :param str config_name: The name of the Maestral configuration to use.
    :param float timeout: Number of sec to wait for daemon to shut down before killing it.
    :returns: ``Exit.Ok`` if successful, ``Exit.Killed`` if killed and ``Exit.NotRunning``
        if the daemon was not running.
    """

    logger.debug("Stopping daemon")
    lockfile = PIDLockFile(pidpath_for_config(config_name))
    pid = lockfile.read_pid()

    if pid:
        try:
            # tell maestral daemon to shut down
            with MaestralProxy(config_name) as m:
                m.stop_sync()
                m.shutdown_pyro_daemon()
        except Pyro5.errors.CommunicationError:
            logger.debug("Could not communicate with daemon")
            try:
                os.kill(pid, signal.SIGTERM)  # try to send SIGTERM to process
                logger.debug("Terminating daemon process")
            except ProcessLookupError:
                logger.debug("Daemon was not running")
                return Exit.NotRunning
        finally:
            # wait for maestral to carry out shutdown
            logger.debug("Waiting for shutdown")
            while timeout > 0:
                try:
                    os.kill(pid, 0)  # query if still running
                except OSError:
                    logger.debug("Daemon shut down")
                    return Exit.Ok  # return True if not running anymore
                else:
                    time.sleep(0.2)  # wait for 0.2 sec and try again
                    timeout -= 0.2

            # send SIGKILL after timeout, delete PID file and return False
            os.kill(pid, signal.SIGKILL)
            logger.debug("Daemon process killed")
            lockfile.break_lock()
            return Exit.Killed
    else:
        return Exit.NotRunning


def get_maestral_proxy(config_name="maestral", fallback=False):
    """
    Returns a Pyro proxy of the a running Maestral instance. If ``fallback`` is
    ``True``, a new instance of Maestral will be returned when the daemon cannot be
    reached.

    :param str config_name: The name of the Maestral configuration to use.
    :param bool fallback: If ``True``, a new instance of Maestral will be returned when
        the daemon cannot be reached. Defaults to ``False``.
    :returns: Pyro proxy of Maestral or a new instance.
    :raises: ``Pyro5.errors.CommunicationError`` if the daemon cannot be reached and
        ``fallback`` is ``False``.
    """

    pid = get_maestral_pid(config_name)

    if pid:
        sock_name = sockpath_for_config(config_name)

        sys.excepthook = Pyro5.errors.excepthook
        maestral_daemon = Proxy(URI.format(config_name, "./u:" + sock_name))
        try:
            maestral_daemon._pyroBind()
            return maestral_daemon
        except Pyro5.errors.CommunicationError:
            maestral_daemon._pyroRelease()

    if fallback:
        from maestral.sync.main import Maestral
        m = Maestral(config_name, run=False)
        m._log_handler_stream.setLevel(logging.CRITICAL)
        return m
    else:
        raise Pyro5.errors.CommunicationError


class MaestralProxy(object):
    """A context manager to open and close a Proxy to the Maestral daemon."""

    def __init__(self, config_name="maestral", fallback=False):
        self.m = get_maestral_proxy(config_name, fallback)

    def __enter__(self):
        return self.m

    def __exit__(self, exc_type, exc_value, traceback):
        if isinstance(self.m, Proxy):
            self.m._pyroRelease()

        del self.m
