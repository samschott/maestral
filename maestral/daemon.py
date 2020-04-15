# -*- coding: utf-8 -*-
"""
@author: Sam Schott  (ss2151@cam.ac.uk)

(c) Sam Schott; This work is licensed under a Creative Commons
Attribution-NonCommercial-NoDerivs 2.0 UK: England & Wales License.

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

# external imports
import Pyro5.errors
from Pyro5.api import Daemon, Proxy, expose, oneway
from Pyro5.serializers import SerpentSerializer
from lockfile.pidlockfile import PIDLockFile, AlreadyLocked

# local imports
from maestral.errors import MaestralApiError, SYNC_ERRORS, FATAL_ERRORS
from maestral.constants import IS_FROZEN


threads = dict()
URI = 'PYRO:maestral.{0}@{1}'


class Exit(enum.Enum):
    """Enumeration of daemon exit results."""
    Ok = 0
    Killed = 1
    NotRunning = 2
    Failed = 3


class Start(enum.Enum):
    """Enumeration of daemon start results."""
    Ok = 0
    AlreadyRunning = 1
    Failed = 2


# ==== error serialization ===============================================================

def serpent_deserialize_api_error(class_name, d):
    """
    Deserializes a :class:`errors.MaestralApiError`.

    :param str class_name: Name of class to deserialize.
    :param dict d: Dictionary of serialized class.
    :returns: Class instance.
    :rtype: :class:`errors.MaestralApiError`
    """
    # import maestral errors for evaluation
    import maestral.errors  # noqa: F401

    cls = eval(class_name)
    err = cls(*d['args'])
    for a_name, a_value in d['attributes'].items():
        setattr(err, a_name, a_value)

    return err


for err_cls in list(SYNC_ERRORS) + list(FATAL_ERRORS) + [MaestralApiError]:
    SerpentSerializer.register_dict_to_class(
        err_cls.__module__ + '.' + err_cls.__name__,
        serpent_deserialize_api_error
    )


# ==== helpers for daemon management =====================================================

def _escape_spaces(string):
    return string.replace(" ", "_")


def _sigterm_handler(signal_number, frame):
    sys.exit()


def _send_term(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _process_exists(pid):
    try:
        os.kill(pid, signal.SIG_DFL)
        return True
    except ProcessLookupError:
        return False


def sockpath_for_config(config_name):
    """
    Returns the unix socket location to be used for the config. This should default to
    the apps runtime directory + '/maestral/CONFIG_NAME.sock'.
    """
    from maestral.utils.appdirs import get_runtime_path
    return get_runtime_path('maestral', config_name + '.sock')


def pidpath_for_config(config_name):
    from maestral.utils.appdirs import get_runtime_path
    return get_runtime_path('maestral', config_name + '.pid')


def is_pidfile_stale(pidfile):
    """
    Determine whether a PID file is stale. Returns ``True`` if the PID file is stale,
    ``False`` otherwise. The PID file is stale if its contents are valid but do not
    match the PID of a currently-running process.
    """
    result = False

    pid = pidfile.read_pid()
    if pid:
        return not _process_exists(pid)
    else:
        return result


def get_maestral_pid(config_name):
    """
    Returns Maestral's PID if the daemon is running, ``None`` otherwise.

    :param str config_name: The name of the Maestral configuration to use.
    :returns: The daemon's PID.
    :rtype: int
    """

    lockfile = PIDLockFile(pidpath_for_config(config_name))
    pid = lockfile.read_pid()

    if pid and not is_pidfile_stale(lockfile):
        return pid
    else:
        lockfile.break_lock()


def _wait_for_startup(config_name, timeout=8):
    """Waits for the daemon to start and verifies Pyro communication. Returns ``Start.Ok``
    if startup and communication succeeds within timeout, ``Start.Failed`` otherwise."""
    t0 = time.time()
    pid = None

    while not pid and time.time() - t0 < timeout / 2:
        pid = get_maestral_pid(config_name)
        time.sleep(0.2)

    if pid:
        return _check_pyro_communication(config_name, timeout=int(timeout / 2))
    else:
        return Start.Failed


def _check_pyro_communication(config_name, timeout=2):
    """Checks if we can communicate with the maestral daemon. Returns ``Start.Ok`` if
    communication succeeds within timeout, ``Start.Failed``  otherwise."""

    sock_name = sockpath_for_config(config_name)
    maestral_daemon = Proxy(URI.format(_escape_spaces(config_name), './u:' + sock_name))

    # wait until we can communicate with daemon, timeout after :param:`timeout`
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

def start_maestral_daemon(config_name='maestral', log_to_stdout=False):
    """
    Wraps :class:`main.Maestral` as Pyro daemon object, creates a new instance and starts
    Pyro's event loop to listen for requests on a unix domain socket. This call will block
    until the event loop shuts down.

    This command will return silently if the daemon is already running.

    :param str config_name: The name of the Maestral configuration to use.
    :param bool log_to_stdout: If ``True``, write logs to stdout. Defaults to ``False``.
    """
    import threading
    from maestral.main import Maestral

    sock_name = sockpath_for_config(config_name)
    pid_name = pidpath_for_config(config_name)

    lockfile = PIDLockFile(pid_name)

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _sigterm_handler)

    # acquire PID lock file

    try:
        lockfile.acquire()
    except AlreadyLocked:
        if is_pidfile_stale(lockfile):
            lockfile.break_lock()
            lockfile.acquire()
        else:
            return

    # Nice ourselves to give other processes priority. We will likely only
    # have significant CPU usage in case of many concurrent downloads.
    os.nice(10)

    try:
        # clean up old socket
        try:
            os.remove(sock_name)
        except FileNotFoundError:
            pass

        daemon = Daemon(unixsocket=sock_name)

        # expose maestral as Pyro server
        # convert selected methods to one way calls so that they don't block
        ExposedMaestral = expose(Maestral)

        ExposedMaestral.start_sync = oneway(ExposedMaestral.start_sync)
        ExposedMaestral.stop_sync = oneway(ExposedMaestral.stop_sync)
        ExposedMaestral.pause_sync = oneway(ExposedMaestral.pause_sync)
        ExposedMaestral.resume_sync = oneway(ExposedMaestral.resume_sync)
        ExposedMaestral.shutdown_pyro_daemon = oneway(ExposedMaestral.shutdown_pyro_daemon)

        m = ExposedMaestral(config_name, log_to_stdout=log_to_stdout)

        daemon.register(m, f'maestral.{_escape_spaces(config_name)}')
        daemon.requestLoop(loopCondition=m._loop_condition)
        daemon.close()
    except Exception:
        traceback.print_exc()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
    finally:
        lockfile.release()


def start_maestral_daemon_thread(config_name='maestral', log_to_stdout=False):
    """
    Starts the Maestral daemon in a thread (by calling :func:`start_maestral_daemon`).

    :param str config_name: The name of the Maestral configuration to use.
    :param bool log_to_stdout: If ``True``, write logs to stdout. Defaults to ``False``.
    :returns: ``Start.Ok`` if successful, ``Start.AlreadyRunning`` if the daemon was
        already running or ``Start.Failed`` if startup failed.
    """
    import threading

    t = threading.Thread(
        target=start_maestral_daemon,
        args=(config_name, log_to_stdout),
        name=f'maestral-daemon-{config_name}',
        daemon=True,
    )
    t.start()

    threads[config_name] = t

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _sigterm_handler)

    return _wait_for_startup(config_name)


def start_maestral_daemon_process(config_name='maestral', log_to_stdout=False):
    """
    Starts the Maestral daemon in a separate process by calling
    :func:`start_maestral_daemon`.

    This function assumes that ``sys.executable`` points to the Python executable. In
    case of a frozen app, the executable must take the command line argument
    ``--frozen-daemon to start`` a daemon process which is *not syncing*, .i.e., just run
    :meth:`start_maestral_daemon`. This is currently supported through the
    constole_script entry points of both `maestral` and `maestral_qt`.

    Starting a detached daemon process is difficult from a standalone executable since
    the typical double-fork magic may fail on macOS and we do not have acccess to a
    standalone Python interpreter to spawn a subprocess. Our approach mimics the "freeze
    support" implemented by the multiprocessing module but fully detaches the spawned
    process.

    :param str config_name: The name of the Maestral configuration to use.
    :param bool log_to_stdout: If ``True``, write logs to stdout. Defaults to ``False``.
    :returns: ``Start.Ok`` if successful, ``Start.AlreadyRunning`` if the daemon was
        already running or ``Start.Failed`` if startup failed.
    """
    import subprocess
    from shlex import quote
    import multiprocessing as mp

    STD_IN_OUT = subprocess.STDOUT if log_to_stdout else subprocess.DEVNULL

    # use nested Popen and multiprocessing.Process to effectively create double fork
    # see Unix 'double-fork magic'

    if IS_FROZEN:

        def target():
            subprocess.Popen(
                [sys.executable, '--frozen-daemon', '-c', config_name],
                stdin=STD_IN_OUT, stdout=STD_IN_OUT, stderr=STD_IN_OUT,
            )

    else:

        def target():
            # protect against injection
            cc = quote(config_name).strip("'")
            std_log = bool(log_to_stdout)

            cmd = (f'import maestral.daemon; '
                   f'maestral.daemon.start_maestral_daemon("{cc}", {std_log})')

            subprocess.Popen(
                [sys.executable, '-c', cmd],
                stdin=STD_IN_OUT, stdout=STD_IN_OUT, stderr=STD_IN_OUT,
            )

    mp.Process(
        target=target,
        name='maestral-daemon-launcher',
        daemon=True,
    ).start()

    return _wait_for_startup(config_name)


def stop_maestral_daemon_process(config_name='maestral', timeout=10):
    """Stops a maestral daemon process by finding its PID and shutting it down.

    This function first tries to shut down Maestral gracefully. If this fails, it will
    send SIGTERM. If that fails as well, it will send SIGKILL to the process.

    :param str config_name: The name of the Maestral configuration to use.
    :param float timeout: Number of sec to wait for daemon to shut down before killing it.
    :returns: ``Exit.Ok`` if successful, ``Exit.Killed`` if killed and ``Exit.NotRunning``
        if the daemon was not running.
    """

    lockfile = PIDLockFile(pidpath_for_config(config_name))
    pid = lockfile.read_pid()

    try:
        if not pid or not _process_exists(pid):
            return Exit.NotRunning

        try:
            with MaestralProxy(config_name) as m:
                m.stop_sync()
                m.shutdown_pyro_daemon()
        except Pyro5.errors.CommunicationError:
            _send_term(pid)
        finally:
            while timeout > 0:
                if not _process_exists(pid):
                    return Exit.Ok
                else:
                    time.sleep(0.2)
                    timeout -= 0.2

            # send SIGTERM after timeout and delete PID file
            _send_term(pid)

            time.sleep(1)

            if not _process_exists(pid):
                return Exit.Ok
            else:
                os.kill(pid, signal.SIGKILL)
                return Exit.Killed
    finally:
        lockfile.break_lock()


def stop_maestral_daemon_thread(config_name='maestral', timeout=10):
    """Stops a maestral daemon thread without killing the parent process.

    :param str config_name: The name of the Maestral configuration to use.
    :param float timeout: Number of sec to wait for daemon to shut down before killing it.
    :returns: ``Exit.Ok`` if successful,``Exit.NotRunning`` if the daemon was not running,
        ``Exit.Failed`` if it could not be stopped within timeout.
    """

    lockfile = PIDLockFile(pidpath_for_config(config_name))
    t = threads[config_name]

    if not t.is_alive():
        lockfile.break_lock()
        return Exit.NotRunning

    # tell maestral daemon to shut down
    try:
        with MaestralProxy(config_name) as m:
            m.stop_sync()
            m.shutdown_pyro_daemon()
    except Pyro5.errors.CommunicationError:
        return Exit.Failed

    # wait for maestral to carry out shutdown
    t.join(timeout=timeout)
    if t.is_alive():
        return Exit.Failed
    else:
        return Exit.Ok


def get_maestral_proxy(config_name='maestral', fallback=False):
    """
    Returns a Pyro proxy of the a running Maestral instance.

    :param str config_name: The name of the Maestral configuration to use.
    :param bool fallback: If ``True``, a new instance of Maestral will be returned when
        the daemon cannot be reached. Defaults to ``False``.
    :returns: Pyro proxy of Maestral or a new instance.
    :raises: :class:`Pyro5.errors.CommunicationError` if the daemon cannot be reached and
        ``fallback`` is ``False``.
    """

    pid = get_maestral_pid(config_name)

    if pid:
        sock_name = sockpath_for_config(config_name)

        sys.excepthook = Pyro5.errors.excepthook
        maestral_daemon = Proxy(URI.format(_escape_spaces(config_name), './u:' + sock_name))
        try:
            maestral_daemon._pyroBind()
            return maestral_daemon
        except Pyro5.errors.CommunicationError:
            maestral_daemon._pyroRelease()

    if fallback:
        from maestral.main import Maestral
        return Maestral(config_name)
    else:
        raise Pyro5.errors.CommunicationError


class MaestralProxy(object):
    """A context manager to open and close a proxy to the Maestral daemon."""

    def __init__(self, config_name='maestral', fallback=False):
        self.m = get_maestral_proxy(config_name, fallback)

    def __enter__(self):
        return self.m

    def __exit__(self, exc_type, exc_value, tb):
        if isinstance(self.m, Proxy):
            self.m._pyroRelease()

        del self.m
