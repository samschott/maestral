# -*- coding: utf-8 -*-
from typing import Callable


class NetworkConnectionNotifierBase:
    def __init__(self, host: str, callback: Callable) -> None:
        self.host = host
        self.callback = callback

    @property
    def connected(self) -> bool:
        raise NotImplementedError()
