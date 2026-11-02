"""Circuit breaker cho tầng local: 3 lỗi trong 60s thì mở mạch 60s."""

from __future__ import annotations

import time


class CircuitBreaker:
    def __init__(self, threshold: int = 3, window: int = 60, cooldown: int = 60) -> None:
        self.threshold = threshold
        self.window = window
        self.cooldown = cooldown
        self.failures: list[float] = []
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        # Half-open: cho phép một request thăm dò sau khi hết cooldown.
        return time.time() - self.opened_at >= self.cooldown

    def success(self) -> None:
        self.failures.clear()
        self.opened_at = None

    def failure(self) -> None:
        now = time.time()
        self.failures = [t for t in self.failures if now - t < self.window] + [now]
        if len(self.failures) >= self.threshold:
            self.opened_at = now
