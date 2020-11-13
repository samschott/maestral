import asyncio
import logging
import socket
import threading
import time
from typing import Callable, Coroutine, Optional

from dbus_next import BusType  # type: ignore
from dbus_next.aio import MessageBus, ProxyInterface  # type: ignore

from .networkstate_base import NetworkConnectionNotifierBase


logger = logging.getLogger(__name__)

NM_CONNECTIVITY_FULL = 4


class NetworkConnectionNotifierDbus(NetworkConnectionNotifierBase):
    def __init__(self, host: str, callback: Callable) -> None:
        super().__init__(host, callback)
        self._loop = asyncio.get_event_loop()
        self.interface: Optional[ProxyInterface] = None
        self._force_run_in_loop(self._init_dbus())

    def _force_run_in_loop(self, coro: Coroutine) -> None:

        if self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        else:
            self._loop.run_until_complete(coro)

    async def _init_dbus(self) -> None:

        try:
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            introspection = await self.bus.introspect(
                "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"
            )
            self.proxy_object = self.bus.get_proxy_object(
                "org.freedesktop.NetworkManager",
                "/org/freedesktop/NetworkManager",
                introspection,
            )
            self.interface = self.proxy_object.get_interface(
                "org.freedesktop.NetworkManager"
            )
            self.interface.on_state_changed(self._on_state_changed)
        except Exception:
            self.interface = None
            logger.warning("Could not connect to DBUS interface", exc_info=True)

    def _on_state_changed(self, state) -> None:
        self.callback(state == NM_CONNECTIVITY_FULL)

    @property
    def connected(self) -> bool:
        if self.interface:
            res = self.interface.check_connectivity()
            return res == NM_CONNECTIVITY_FULL
        else:
            raise RuntimeError("Could not connect to DBUS interface")


class NetworkConnectionNotifierPolling(NetworkConnectionNotifierBase):
    def __init__(self, host: str, callback: Callable, interval: float = 2.0) -> None:
        super().__init__(host, callback)

        self.interval = interval
        self._old_state: Optional[bool] = None

        self._thread = threading.Thread(
            target=self._polling_worker,
            name="maestral-networkstatus-polling",
            daemon=True,
        )

    def _polling_worker(self) -> None:

        while True:

            state = self.connected

            if state is not self._old_state:
                self.callback(state)

            self._old_state = state
            time.sleep(self.interval)

    @property
    def connected(self) -> bool:
        try:
            host = socket.gethostbyname(self.host)
            s = socket.create_connection((host, 80), 2)
            s.close()
            return True
        except Exception:
            return False
