"""In-process safety limits for the anonymous public query routes."""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from math import ceil
from threading import Lock
from time import monotonic
from typing import Callable, Mapping


@dataclass(frozen=True, slots=True)
class PublicLimitSettings:
    per_minute: int = 120
    per_ip_concurrency: int = 4
    global_concurrency: int = 8

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PublicLimitSettings":
        values = os.environ if env is None else env
        names = {
            "per_minute": "DISCLOSURE_PUBLIC_RATE_PER_MINUTE",
            "per_ip_concurrency": "DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY",
            "global_concurrency": "DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY",
        }
        defaults = cls()
        try:
            parsed = {
                field: int(values.get(name, str(getattr(defaults, field))))
                for field, name in names.items()
            }
        except (TypeError, ValueError) as exc:
            raise ValueError("public limit values must be integers") from exc
        if any(value < 1 or value > 10_000 for value in parsed.values()):
            raise ValueError("public limit values must be between 1 and 10000")
        return cls(**parsed)


@dataclass(frozen=True, slots=True)
class Admission:
    allowed: bool
    status_code: int = 200
    detail: str = "ok"
    retry_after: int = 0


@dataclass(slots=True)
class _ClientState:
    timestamps: deque[float]
    active: int = 0


class PublicRequestLimiter:
    def __init__(
        self,
        settings: PublicLimitSettings,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.settings = settings
        self._clock = clock
        self._lock = Lock()
        self._clients: dict[str, _ClientState] = {}
        self._global_active = 0

    def try_acquire(self, client_ip: str, *, now: float | None = None) -> Admission:
        observed = self._clock() if now is None else now
        cutoff = observed - 60.0
        with self._lock:
            for ip, known in list(self._clients.items()):
                while known.timestamps and known.timestamps[0] <= cutoff:
                    known.timestamps.popleft()
                if known.active == 0 and not known.timestamps:
                    del self._clients[ip]

            state = self._clients.setdefault(client_ip, _ClientState(deque()))
            if len(state.timestamps) >= self.settings.per_minute:
                retry_after = max(1, ceil(state.timestamps[0] + 60.0 - observed))
                return Admission(False, 429, "rate_limited", retry_after)
            if state.active >= self.settings.per_ip_concurrency:
                return Admission(False, 429, "ip_busy", 1)
            if self._global_active >= self.settings.global_concurrency:
                if state.active == 0 and not state.timestamps:
                    del self._clients[client_ip]
                return Admission(False, 503, "server_busy", 1)

            state.timestamps.append(observed)
            state.active += 1
            self._global_active += 1
            return Admission(True)

    def release(self, client_ip: str) -> None:
        with self._lock:
            state = self._clients.get(client_ip)
            if state is None or state.active == 0:
                return
            state.active -= 1
            self._global_active = max(0, self._global_active - 1)
            if state.active == 0 and not state.timestamps:
                del self._clients[client_ip]

    @property
    def tracked_ip_count(self) -> int:
        with self._lock:
            return len(self._clients)
