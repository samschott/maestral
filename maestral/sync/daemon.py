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

# external packages
import Pyro4

logger = logging.getLogger(__name__)
URI = "PYRO:maestral.{0}@{1}"


def _get_sock_name(config_name):
    """
    Returns the unix socket location to be used for the config. This should default to
    the apps runtime directory + '/maestral/CONFIG_NAME.sock'.

    :param str config_name: Name of config.
    :return: Socket location.
    :rtype: str
    """
    os.environ["MAESTRAL_CONFIG"] = config_name

    from maestral.sync.utils.app_dirs import get_runtime_path
    return get_runtime_path("maestral", config_name + ".sock")


def _write_pid(config_name):
    """
    Writes the PID to the appropriate file for the given config name.

    :param str config_name: Name of config.
    """
    from maestral.sync.utils.app_dirs import get_runtime_path
    pid_file = get_runtime_path("maestral", config_name + ".pid")
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    logger.debug("PID file written to '{}'.".format(pid_file))


def _read_pid(config_name):
    """
    Reads the PID of the current process from the appropriate file for the given
    config name.

    :param str config_name: Name of config.
    """
    from maestral.sync.utils.app_dirs import get_runtime_path
    pid_file = get_runtime_path("maestral", config_name + ".pid")
    with open(pid_file, "r") as f:
        pid = f.read().split("\n")[0]  # ignore all new lines
    pid = int(pid)

    logger.debug("PID {} read from '{}'.".format(pid, pid_file))

    return pid


def _delete_pid(config_name):
    """
    Reads the PID of the current process to the appropriate file for the given
    config name.

    :param str config_name: Name of config.
    """
    from maestral.sync.utils.app_dirs import get_runtime_path
    pid_file = get_runtime_path("maestral", config_name + ".pid")
    os.unlink(pid_file)

    logger.debug("Removed PID file '{}'.".format(pid_file))


def start_maestral_daemon(config_name, run=True):
    """
    Wraps :class:`maestral.main.Maestral` as Pyro daemon object, creates a new instance
    and start Pyro's event loop to listen for requests on 'localhost'. This call will
    block until the event loop shuts down.

    This command will create a new daemon on each run. Take care not to sync the same
    directory with multiple instances of Meastral! You can use `get_maestral_process_info`
    to check if either a Meastral gui or daemon is already running for the given
    `config_name`.

    :param str config_name: The name of maestral configuration to use.
    :param bool run: If ``True``, start syncing automatically. Defaults to ``True``.
    """

    os.environ["MAESTRAL_CONFIG"] = config_name

    from maestral.sync.main import Maestral
    sock_name = _get_sock_name(config_name)

    logger.debug("Starting Maestral daemon on socket '{}'".format(sock_name))

    try:
        os.remove(sock_name)
    except FileNotFoundError:
        pass

    daemon = Pyro4.Daemon(unixsocket=sock_name)

    _write_pid(config_name)  # write PID to file

    try:
        # we wrap this in a try-except block to make sure that the PID file is always
        # removed, even when Maestral crashes for some reason

        ExposedMaestral = Pyro4.expose(Maestral)
        m = ExposedMaestral(run=run)

        daemon.register(m, "maestral.{}".format(config_name))
        daemon.requestLoop(loopCondition=m._loop_condition)
        daemon.close()
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        _delete_pid(config_name)  # remove PID file


def start_maestral_daemon_thread(config_name):
    """

    Starts the Maestral daemon in a thread (by calling `start_maestral_daemon`).
    This command will create a new daemon on each run. Take care not to sync the same
    directory with multiple instances of Meastral! You can use `get_maestral_process_info`
    to check if either a Meastral gui or daemon is already running for the given
    `config_name`.

    :param str config_name: The name of maestral configuration to use.
    :returns: ``True`` if started, ``False`` otherwise.
    """
    import threading

    t = threading.Thread(
        target=start_maestral_daemon,
        args=(config_name, ),
        daemon=True,
        name="Maestral daemon",
    )
    t.start()

    time.sleep(0.2)
    if t.is_alive():
        logger.debug("Started Maestral daemon thread")
        return True
    else:
        logger.error("Could not start Maestral daemon thread")
        return False


def start_maestral_daemon_process(config_name, log_to_console=False):
    """Starts the Maestral daemon as a separate process (by calling
    `start_maestral_daemon`).

    This command will create a new daemon on each run. Take care not to sync the same
    directory with multiple instances of Meastral! You can use `get_maestral_process_info`
    to check if either a Meastral gui or daemon is already running for the given
    `config_name`.

    :param str config_name: The name of maestral configuration to use.
    :param bool log_to_console: Do not suppress stdout if ``True``, defaults to ``False``.
    :returns: ``True`` if started, ``False`` otherwise.
    """
    import subprocess

    STD_IN_OUT = None if log_to_console else subprocess.DEVNULL

    subprocess.Popen(
        ["maestral", "start", "-f", "-c", config_name],
        stdin=STD_IN_OUT, stdout=STD_IN_OUT, stderr=STD_IN_OUT,
    )

    # wait until process is created, timeout after 2 sec
    t0 = time.time()
    pid = None

    while not pid and time.time() - t0 < 2:
        pid = get_maestral_pid(config_name)

    if pid:
        return _check_pyro_communication(config_name, timeout=1)
    else:
        logger.error("Could not start Maestral daemon process")
        return False


def stop_maestral_daemon_process(config_name="maestral", timeout=5):
    """Stops maestral by finding its PID and shutting it down.

    This function first tries to shut down Maestral gracefully. If this fails, it will
    send SIGTERM. If that fails as well, it will send SIGKILL.

    :param str config_name: The name of maestral configuration to use.
    :param int timeout: Number of sec to wait for daemon to shut down before killing it.
    :returns: ``True`` if terminated gracefully, ``False`` if killed and ``None`` if the
        daemon was not running.
    """
    import signal
    import time

    logger.debug("Stopping daemon")

    pid = get_maestral_pid(config_name)
    if pid:
        try:
            # tell maestral daemon to shut down
            with MaestralProxy(config_name) as m:
                m.stop_sync()
                m.shutdown_daemon()
        except Pyro4.errors.CommunicationError:
            logger.debug("Could not communicate with daemon")
            try:
                # try to send SIGTERM to process
                os.kill(pid, signal.SIGTERM)
                logger.debug("Terminating daemon process")
            except ProcessLookupError:
                # delete PID file and return ``None`` if process does not exist
                _delete_pid(config_name)
                logger.debug("Daemon was not running")
                return
        finally:
            # wait for maestral to carry out shutdown
            logger.debug("Waiting for shutdown")
            t0 = time.time()
            while True:
                try:
                    # query if still running
                    os.kill(pid, 0)
                except OSError:
                    # return ``True`` if not running anymore
                    logger.debug("Daemon shut down")
                    return True
                else:
                    # wait for 0.2 sec and try again
                    time.sleep(0.2)
                    if time.time() - t0 > timeout:
                        # send SIGKILL after timeout, delete PID file and return ``False``
                        os.kill(pid, signal.SIGKILL)
                        logger.debug("Daemon process killed")
                        return False


def get_maestral_daemon_proxy(config_name="maestral", fallback=False):
    """
    Returns a proxy of the running Maestral daemon. If fallback == True,
    a new instance of Maestral will be returned when the daemon cannot be reached.
    """

    os.environ["MAESTRAL_CONFIG"] = config_name

    pid = get_maestral_pid(config_name)

    if pid:

        from maestral.sync.utils.app_dirs import get_runtime_path
        sock_name = get_runtime_path("maestral", config_name + ".sock")

        sys.excepthook = Pyro4.util.excepthook
        maestral_daemon = Pyro4.Proxy(URI.format(config_name, "./u:" + sock_name))
        try:
            maestral_daemon._pyroBind()
            return maestral_daemon
        except Pyro4.errors.CommunicationError:
            maestral_daemon._pyroRelease()

    if fallback:
        from maestral.sync.main import Maestral
        m = Maestral(run=False)
        return m
    else:
        raise Pyro4.errors.CommunicationError


class MaestralProxy(object):
    """A context manager to open and close a Proxy to the Maestral daemon."""

    def __init__(self, config_name="maestral", fallback=False):
        self.m = get_maestral_daemon_proxy(config_name, fallback)

    def __enter__(self):
        return self.m

    def __exit__(self, exc_type, exc_value, traceback):
        if hasattr(self.m, "_pyroRelease"):
            self.m._pyroRelease()

        del self.m


def get_maestral_pid(config_name):
    """
    Returns Maestral's PID if the daemon is running and responsive, ``None`` otherwise.

    If the daemon is unresponsive, it will be killed before returning ``None``.
    """
    import signal

    try:
        pid = _read_pid(config_name)
        logger.debug("Could not find PID file")
    except Exception:
        return None

    try:
        # test if the daemon process receives signals
        os.kill(pid, 0)
    except ProcessLookupError:
        logger.debug("Daemon process with PID {} does not exist.".format(pid))
        # if the process does not exist, delete pid file
        try:
            _delete_pid(config_name)
        except Exception:
            pass
        return None
    except OSError:
        logger.debug("Daemon process with PID {} is not responsive. Killing.".format(pid))
        # if the process does not respond, try to kill it
        os.kill(pid, signal.SIGKILL)
        try:
            _delete_pid(config_name)
        except Exception:
            pass
        return None
    else:
        # everything ok, return process info
        logger.debug("Found Maestral daemon with PID {}.".format(pid))
        return pid


def _check_pyro_communication(config_name, timeout=1):
    """Checks if we can communicate with the maestral daemon."""

    sock_name = _get_sock_name(config_name)
    maestral_daemon = Pyro4.Proxy(URI.format(config_name, "./u:" + sock_name))

    t0 = time.time()
    # wait until we can communicate with daemon, timeout after :param:`timeout`
    while time.time() - t0 < timeout:
        try:
            maestral_daemon._pyroBind()
            logger.debug("Started Maestral daemon process")
            return True
        except Exception:
            time.sleep(0.1)
        finally:
            maestral_daemon._pyroRelease()

    logger.error("Could communicate with Maestral daemon process")
    return False
