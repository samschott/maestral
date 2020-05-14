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
import threading
import fcntl
import struct
import tempfile

# external imports
import Pyro5.errors
from Pyro5.api import Daemon, Proxy, expose, oneway
from Pyro5.serializers import SerpentSerializer
from fasteners import InterProcessLock

# local imports
from maestral.errors import SYNC_ERRORS, FATAL_ERRORS
from maestral.constants import IS_FROZEN
from maestral.utils.appdirs import get_runtime_path


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


for err_cls in list(SYNC_ERRORS) + list(FATAL_ERRORS):
    SerpentSerializer.register_dict_to_class(
        err_cls.__module__ + '.' + err_cls.__name__,
        serpent_deserialize_api_error
    )


# ==== interprocess locking ==============================================================

def _get_lockdata():

    try:
        os.O_LARGEFILE
    except AttributeError:
        start_len = 'll'
    else:
        start_len = 'qq'

    if (sys.platform.startswith(('netbsd', 'freebsd', 'openbsd'))
            or sys.platform == 'darwin'):
        if struct.calcsize('l') == 8:
            off_t = 'l'
            pid_t = 'i'
        else:
            off_t = 'lxxxx'
            pid_t = 'l'

        fmt = off_t + off_t + pid_t + 'hh'
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
    elif sys.platform.startswith('linux'):
        fmt = 'hh' + start_len + 'ih'
        pid_index = 4
        lockdata = struct.pack(fmt, fcntl.F_WRLCK, 0, 0, 0, 0, 0)
    else:
        raise RuntimeError(f'Unsupported platform {sys.platform}')

    return lockdata, fmt, pid_index


class Lock:
    """
    A inter-process and inter-thread lock. This reuses uses code from oslo.concurrency
    but provides non-blocking acquire. Use the :meth:`singleton` class method to retrieve
    an existing instance for thread-safe usage.
    """

    _instances = dict()
    _singleton_lock = threading.Lock()

    @classmethod
    def singleton(cls, name, lock_path=None):

        with cls._singleton_lock:
            try:
                instance = cls._instances[name]
            except KeyError:
                instance = cls(name, lock_path)
                cls._instances[name] = instance

            return instance

    def __init__(self, name, lock_path=None):

        self.name = name
        dirname = lock_path or tempfile.gettempdir()
        lock_path = os.path.join(dirname, name)

        self._internal_lock = threading.Semaphore()
        self._external_lock = InterProcessLock(lock_path)

        self._lock = threading.RLock()

    def acquire(self):
        """
        Attempts to acquire the given lock.

        :returns: Whether or not the acquisition succeeded.
        :rtype: bool
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

    def release(self):
        """Release the previously acquired lock."""
        with self._lock:
            self._external_lock.release()
            self._internal_lock.release()

    def locked(self):
        """Checks if the lock is currently held by any thread or process."""
        with self._lock:
            gotten = self.acquire()
            if gotten:
                self.release()
            return not gotten

    def locking_pid(self):
        """
        Returns the PID of the process which currently holds the lock or None.

        :returns: The PID of the process which currently holds the lock or None.
        :rtype: int
        """

        with self._lock:

            if self._external_lock.acquired:
                return os.getpid()

            try:
                # don't close again in case we are the locking process
                self._external_lock._do_open()
                lockdata, fmt, pid_index = _get_lockdata()
                lockdata = fcntl.fcntl(self._external_lock.lockfile,
                                       fcntl.F_GETLK, lockdata)

                lockdata_list = struct.unpack(fmt, lockdata)
                pid = lockdata_list[pid_index]

                if pid > 0:
                    return pid

            except OSError:
                pass


# ==== helpers for daemon management =====================================================

def _escape_spaces(string):
    return string.replace(' ', '_')


def _sigterm_handler(signal_number, frame):
    sys.exit()


def _send_term(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


class MaestralLock:
    """
    A inter-process and inter-thread lock for Maestral.
    """

    def __new__(cls, config_name):
        name = f'{config_name}.lock'
        path = get_runtime_path('maestral')
        return Lock.singleton(name, path)


def sockpath_for_config(config_name):
    """
    Returns the unix socket location to be used for the config. This should default to
    the apps runtime directory + '/maestral/CONFIG_NAME.sock'.
    """
    return get_runtime_path('maestral', f'{config_name}.sock')


def lockpath_for_config(config_name):
    return get_runtime_path('maestral',  f'{config_name}.lock')


def get_maestral_pid(config_name):
    """
    Returns Maestral's PID if the daemon is running, ``None`` otherwise.

    :param str config_name: The name of the Maestral configuration to use.
    :returns: The daemon's PID.
    :rtype: int
    """

    return MaestralLock(config_name).locking_pid()


def _wait_for_startup(config_name, timeout=8):
    """Checks if we can communicate with the maestral daemon. Returns ``Start.Ok`` if
    communication succeeds within timeout, ``Start.Failed``  otherwise."""

    sock_name = sockpath_for_config(config_name)
    maestral_daemon = Proxy(URI.format(_escape_spaces(config_name), './u:' + sock_name))

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

    sockpath = sockpath_for_config(config_name)

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _sigterm_handler)

    # acquire PID lock file
    lock = MaestralLock(config_name)
    if not lock.acquire():
        raise RuntimeError('Maestral daemon is already running')

    # Nice ourselves to give other processes priority. We will likely only
    # have significant CPU usage in case of many concurrent downloads.
    os.nice(10)

    try:
        # clean up old socket
        try:
            os.remove(sockpath)
        except FileNotFoundError:
            pass

        # expose maestral as Pyro server
        # convert selected methods to one way calls so that they don't block
        ExposedMaestral = expose(Maestral)

        ExposedMaestral.start_sync = oneway(ExposedMaestral.start_sync)
        ExposedMaestral.stop_sync = oneway(ExposedMaestral.stop_sync)
        ExposedMaestral.pause_sync = oneway(ExposedMaestral.pause_sync)
        ExposedMaestral.resume_sync = oneway(ExposedMaestral.resume_sync)
        ExposedMaestral.shutdown_pyro_daemon = oneway(ExposedMaestral.shutdown_pyro_daemon)

        m = ExposedMaestral(config_name, log_to_stdout=log_to_stdout)

        with Daemon(unixsocket=sockpath) as daemon:
            daemon.register(m, f'maestral.{_escape_spaces(config_name)}')
            daemon.requestLoop(loopCondition=m._loop_condition)

    except Exception:
        traceback.print_exc()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
    finally:
        lock.release()


def start_maestral_daemon_thread(config_name='maestral', log_to_stdout=False):
    """
    Starts the Maestral daemon in a thread (by calling :func:`start_maestral_daemon`).

    :param str config_name: The name of the Maestral configuration to use.
    :param bool log_to_stdout: If ``True``, write logs to stdout. Defaults to ``False``.
    :returns: ``Start.Ok`` if successful, ``Start.AlreadyRunning`` if the daemon was
        already running or ``Start.Failed`` if startup failed.
    """

    if MaestralLock(config_name).locking_pid():
        return Start.AlreadyRunning

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

    if MaestralLock(config_name).locking_pid():
        return Start.AlreadyRunning

    from shlex import quote
    import multiprocessing as mp

    # use nested Popen and multiprocessing.Process to effectively create double fork
    # see Unix 'double-fork magic'

    if IS_FROZEN:

        def target():
            subprocess.Popen([sys.executable, '--frozen-daemon', '-c', config_name])

    else:

        def target():
            # protect against injection
            cc = quote(config_name).strip("'")
            std_log = bool(log_to_stdout)

            cmd = (f'import maestral.daemon; '
                   f'maestral.daemon.start_maestral_daemon("{cc}", {std_log})')

            subprocess.Popen([sys.executable, '-c', cmd])

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

    pid = get_maestral_pid(config_name)

    if not pid:
        return Exit.NotRunning

    try:
        with MaestralProxy(config_name) as m:
            m.stop_sync()
            m.shutdown_pyro_daemon()
    except Pyro5.errors.CommunicationError:
        _send_term(pid)
    finally:
        while timeout > 0:
            if not get_maestral_pid(config_name):
                return Exit.Ok
            else:
                time.sleep(0.2)
                timeout -= 0.2

        # send SIGTERM after timeout and delete PID file
        _send_term(pid)

        time.sleep(1)

        if not get_maestral_pid(config_name):
            return Exit.Ok
        else:
            os.kill(pid, signal.SIGKILL)
            return Exit.Killed


def stop_maestral_daemon_thread(config_name='maestral', timeout=10):
    """Stops a maestral daemon thread without killing the parent process.

    :param str config_name: The name of the Maestral configuration to use.
    :param float timeout: Number of sec to wait for daemon to shut down before killing it.
    :returns: ``Exit.Ok`` if successful,``Exit.NotRunning`` if the daemon was not running,
        ``Exit.Failed`` if it could not be stopped within timeout.
    """

    if not MaestralLock(config_name).locking_pid():
        return Exit.NotRunning

    # tell maestral daemon to shut down
    try:
        with MaestralProxy(config_name) as m:
            m.stop_sync()
            m.shutdown_pyro_daemon()
    except Pyro5.errors.CommunicationError:
        return Exit.Failed

    # wait for maestral to carry out shutdown
    t = threads.get(config_name)
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


class MaestralProxy:
    """A context manager to open and close a proxy to the Maestral daemon."""

    def __init__(self, config_name='maestral', fallback=False):
        self.m = get_maestral_proxy(config_name, fallback)

    def __enter__(self):
        return self.m

    def __exit__(self, exc_type, exc_value, tb):
        if isinstance(self.m, Proxy):
            self.m._pyroRelease()

        del self.m
