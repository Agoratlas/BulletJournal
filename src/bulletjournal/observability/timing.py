from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_METRIC_NAMES = ('app', 'db', 'disk', 'proxy')
_collector: ContextVar[RequestTiming | None] = ContextVar('request_timing_collector', default=None)


class RequestTiming:
    def __init__(self) -> None:
        self.durations = {name: 0.0 for name in _METRIC_NAMES}
        self.active_metrics: set[str] = set()

    def add(self, name: str, elapsed_seconds: float) -> None:
        self.durations[name] += elapsed_seconds

    def server_timing_header(self) -> str:
        return ', '.join(
            f'{name};dur={duration * 1000:.1f}' for name, duration in self.durations.items() if duration > 0
        )


def begin_request_timing() -> tuple[RequestTiming, object]:
    collector = RequestTiming()
    return collector, _collector.set(collector)


def end_request_timing(token: object) -> None:
    _collector.reset(token)


class ServerTimingMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        collector, token = begin_request_timing()
        started_at = time.perf_counter()

        async def send_with_timing(message) -> None:
            if message['type'] == 'http.response.start':
                collector.add('app', time.perf_counter() - started_at)
                message['headers'].append((b'server-timing', collector.server_timing_header().encode('ascii')))
            await send(message)

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            end_request_timing(token)


@contextmanager
def measure(name: str) -> Iterator[None]:
    collector = _collector.get()
    if collector is None or name in collector.active_metrics:
        yield
        return
    started_at = time.perf_counter()
    collector.active_metrics.add(name)
    try:
        yield
    finally:
        collector.add(name, time.perf_counter() - started_at)
        collector.active_metrics.remove(name)
