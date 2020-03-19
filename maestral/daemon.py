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

# external imports
import Pyro5.errors
from Pyro5.api import Daemon, Proxy, expose, oneway
from Pyro5.serializers import SerpentSerializer
from lockfile.pidlockfile import PIDLockFile, AlreadyLocked, LockTimeout

# local imports
from maestral.errors import MaestralApiError, SYNC_ERRORS, FATAL_ERRORS


_threads = dict()


logger = logging.getLogger(__name__)
URI = 'PYRO:maestral.{0}@{1}'


class Exit(enum.Enum):
    Ok = 0
    Killed = 1
    NotRunning = 2
    Failed = 3


class Start(enum.Enum):
    Ok = 0
    AlreadyRunning = 1
    Failed = 2


# ==== error serialization ===============================================================

def serpent_deserialize_api_error(class_name, d):
    # import maestral errors for evaluation
    import maestral.errors  # noqa: F401

    cls = eval(class_name)
    e = cls(*d['args'])
    for a_name, a_value in d['attributes'].items():
        setattr(e, a_name, a_value)

    return e


for err_cls in list(SYNC_ERRORS) + list(FATAL_ERRORS) + [MaestralApiError]:
    SerpentSerializer.register_dict_to_class(
        err_cls.__module__ + '.' + err_cls.__name__,
        serpent_deserialize_api_error
    )


# ==== helpers for daemon management =====================================================


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

    logger.debug(f'Waiting for process with pid {pid} to start.')

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
    maestral_daemon = Proxy(URI.format(config_name, './u:' + sock_name))

    # wait until we can communicate with daemon, timeout after :param:`timeout`
    while timeout > 0:
        try:
            maestral_daemon._pyroBind()
            logger.debug('Successfully communication with daemon')
            return Start.Ok
        except Exception:
            time.sleep(0.2)
            timeout -= 0.2
        finally:
            maestral_daemon._pyroRelease()

    logger.error('Could not communicate with Maestral daemon')
    return Start.Failed


# ==== main functions to manage daemon ===================================================

def run_maestral_daemon(config_name='maestral', run=True, log_to_stdout=False):
    """
    Wraps :class:`maestral.main.Maestral` as Pyro daemon object, creates a new instance
    and start Pyro's event loop to listen for requests on a unix domain socket. This call
    will block until the event loop shuts down.

    This command will return silently if the daemon is already running.

    :param str config_name: The name of the Maestral configuration to use.
    :param bool run: If ``True``, start syncing automatically. Defaults to ``True``.
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
        lockfile.acquire(timeout=1)
    except (AlreadyLocked, LockTimeout):
        if is_pidfile_stale(lockfile):
            lockfile.break_lock()
        else:
            logger.debug(f'Maestral already running')
            return

    # Nice ourselves give other processes priority. We will likely only
    # have significant CPU usage in case of many concurrent downloads.
    os.nice(10)

    logger.debug(f'Starting Maestral daemon on socket "{sock_name}"')

    try:
        # clean up old socket
        try:
            os.remove(sock_name)
        except FileNotFoundError:
            pass

        daemon = Daemon(unixsocket=sock_name)

        # start Maestral as Pyro server
        ExposedMaestral = expose(Maestral)
        # mark stop_sync and shutdown_daemon as one way
        # methods so that they don't block on call
        ExposedMaestral.stop_sync = oneway(ExposedMaestral.stop_sync)
        ExposedMaestral.pause_sync = oneway(ExposedMaestral.pause_sync)
        ExposedMaestral.shutdown_pyro_daemon = oneway(ExposedMaestral.shutdown_pyro_daemon)
        m = ExposedMaestral(config_name, run=run, log_to_stdout=log_to_stdout)

        daemon.register(m, f'maestral.{config_name}')
        daemon.requestLoop(loopCondition=m._loop_condition)
        daemon.close()
    except Exception:
        traceback.print_exc()
    except (KeyboardInterrupt, SystemExit):
        logger.info('Received system exit')
        sys.exit(0)
    finally:
        lockfile.release()


def start_maestral_daemon_thread(config_name='maestral', run=True):
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

    t = threading.Thread(
        target=run_maestral_daemon,
        args=(config_name, run),
        name=f'maestral-daemon-{config_name}',
        daemon=True,
    )
    t.start()

    _threads[config_name] = t

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _sigterm_handler)

    return _wait_for_startup(config_name, timeout=8)


def start_maestral_daemon_process(config_name='maestral', run=True):
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
    # see Unix 'double-fork magic'

    def target(cc, r):
        cc = quote(cc)
        r = bool(r)
        subprocess.Popen(
            [sys.executable, '-c', f'import maestral.daemon; maestral.daemon.run_maestral_daemon("{cc}", {r})'],
            stdin=STD_IN_OUT, stdout=STD_IN_OUT, stderr=STD_IN_OUT,
        )

    mp.Process(
        target=target,
        args=(config_name, run),
        name='maestral-daemon-launcher',
        daemon=True,
    ).start()

    return _wait_for_startup(config_name, timeout=8)


def stop_maestral_daemon_process(config_name='maestral', timeout=10):
    """Stops maestral by finding its PID and shutting it down.

    This function first tries to shut down Maestral gracefully. If this fails, it will
    send SIGTERM. If that fails as well, it will send SIGKILL.

    :param str config_name: The name of the Maestral configuration to use.
    :param float timeout: Number of sec to wait for daemon to shut down before killing it.
    :returns: ``Exit.Ok`` if successful, ``Exit.Killed`` if killed and ``Exit.NotRunning``
        if the daemon was not running.
    """

    logger.debug('Stopping daemon')
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
            logger.debug('Could not communicate with daemon, sending SIGTERM')
            _send_term(pid)
        finally:
            logger.debug('Waiting for shutdown')
            while timeout > 0:
                if not _process_exists(pid):
                    logger.debug('Daemon shut down')
                    return Exit.Ok
                else:
                    time.sleep(0.2)
                    timeout -= 0.2

            # send SIGTERM after timeout and delete PID file
            _send_term(pid)

            time.sleep(1)

            if not _process_exists(pid):
                logger.debug('Daemon shut down')
                return Exit.Ok
            else:
                os.kill(pid, signal.SIGKILL)
                logger.debug('Daemon killed')
                return Exit.Killed
    finally:
        lockfile.break_lock()


def stop_maestral_daemon_thread(config_name='maestral', timeout=10):
    """Stops maestral's thread.

    This function tries to shut down Maestral gracefully. If it is not successful
    within the given timeout, a TimeoutError is raised.

    :param str config_name: The name of the Maestral configuration to use.
    :param float timeout: Number of sec to wait for daemon to shut down before killing it.
    :returns: ``Exit.Ok`` if successful,``Exit.NotRunning`` if the daemon was not running,
        ``Exit.Failed`` if it could not be stopped within  timeout.
    """

    logger.debug('Stopping thread')
    lockfile = PIDLockFile(pidpath_for_config(config_name))
    t = _threads[config_name]

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
        maestral_daemon = Proxy(URI.format(config_name, './u:' + sock_name))
        try:
            maestral_daemon._pyroBind()
            return maestral_daemon
        except Pyro5.errors.CommunicationError:
            maestral_daemon._pyroRelease()

    if fallback:
        from maestral.main import Maestral
        m = Maestral(config_name, run=False)
        m.log_handler_stream.setLevel(logging.CRITICAL)
        return m
    else:
        raise Pyro5.errors.CommunicationError


class MaestralProxy(object):
    """A context manager to open and close a Proxy to the Maestral daemon."""

    def __init__(self, config_name='maestral', fallback=False):
        self.m = get_maestral_proxy(config_name, fallback)

    def __enter__(self):
        return self.m

    def __exit__(self, exc_type, exc_value, tb):
        if isinstance(self.m, Proxy):
            self.m._pyroRelease()

        del self.m
