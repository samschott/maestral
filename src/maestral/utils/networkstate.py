# -*- coding: utf-8 -*-
"""

A module to receive notifications on network status changes. This will aim to use non-
polling implementations (SCNetworkReachability on macOS and the
org.freedesktop.NetworkManager Dbus services on Linux) and fall back to polling
otherwise.

"""
import logging
from typing import Callable, Optional
import platform

from .networkstate_base import NetworkConnectionNotifierBase

logger = logging.getLogger(__name__)


__all__ = ["NetworkConnectionNotifier"]


class NetworkConnectionNotifier:
    """
    :param host: Host address to check connection.
    :param on_connect: Callback to invoke when connection is lost.
    :param on_disconnect: Callback to invoke when connection is established.
    """

    _impl: NetworkConnectionNotifierBase

    def __init__(
        self,
        host: str = "www.google.com",
        on_connect: Optional[Callable] = None,
        on_disconnect: Optional[Callable] = None,
    ) -> None:

        self.host = host
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect

        if platform.system() == "Darwin":
            from .networkstate_macos import NetworkConnectionNotifierMacOS

            self._impl = NetworkConnectionNotifierMacOS(self.host, self._callback)

        elif platform.system() == "Linux":
            from .networkstate_linux import (
                NetworkConnectionNotifierDbus,
                NetworkConnectionNotifierPolling,
            )

            self._impl = NetworkConnectionNotifierDbus(self.host, self._callback)

            if not self._impl.interface:
                self._impl = NetworkConnectionNotifierPolling(self.host, self._callback)

        else:
            raise RuntimeError(f"Unsupported platform {platform.platform()}")

    @property
    def connected(self) -> bool:
        return self._impl.connected

    def _callback(self, connected):

        if connected and self.on_connect:
            self.on_connect()
        elif self.on_disconnect:
            self.on_disconnect()
