from __future__ import annotations

import fcntl
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, ClassVar


class ProjectLockTimeout(TimeoutError):
    pass


@dataclass(slots=True)
class _BarrierState:
    readers: dict[int, int] = field(default_factory=dict)
    writer: int | None = None
    write_depth: int = 0


class ProjectLock:
    """Process-local read/write barrier backed by an interprocess advisory lock."""

    _registry_guard = threading.Lock()
    _barriers: ClassVar[dict[Path, tuple[threading.Condition, _BarrierState]]] = {}

    def __init__(self, path: Path):
        self.path = path.resolve()
        with self._registry_guard:
            if self.path not in self._barriers:
                self._barriers[self.path] = (
                    threading.Condition(threading.RLock()),
                    _BarrierState(),
                )
            self._condition, self._state = self._barriers[self.path]

    @contextmanager
    def shared(self, *, timeout: float | None = None) -> Iterator[None]:
        thread_id = threading.get_ident()
        with self._condition:
            self._wait_for(lambda: self._state.writer in (None, thread_id), timeout)
            readers = self._state.readers
            readers[thread_id] = int(readers.get(thread_id, 0)) + 1
        handle: IO[bytes] | None = None
        try:
            if self._state.writer != thread_id:
                handle = self._flock(fcntl.LOCK_SH, timeout)
            yield
        finally:
            if handle is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            with self._condition:
                readers = self._state.readers
                depth = int(readers[thread_id]) - 1
                if depth:
                    readers[thread_id] = depth
                else:
                    del readers[thread_id]
                self._condition.notify_all()

    @contextmanager
    def exclusive(self, *, timeout: float | None = None) -> Iterator[None]:
        thread_id = threading.get_ident()
        handle: IO[bytes] | None = None
        with self._condition:
            readers = self._state.readers
            if readers.get(thread_id) and self._state.writer != thread_id:
                raise RuntimeError('Cannot upgrade a shared project lock to exclusive.')
            self._wait_for(
                lambda: self._state.writer in (None, thread_id) and (not readers or set(readers) == {thread_id}),
                timeout,
            )
            reentrant = self._state.writer == thread_id
            self._state.writer = thread_id
            self._state.write_depth += 1
        try:
            if not reentrant:
                handle = self._flock(fcntl.LOCK_EX, timeout)
            yield
        finally:
            if handle is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            with self._condition:
                self._state.write_depth -= 1
                if self._state.write_depth == 0:
                    self._state.writer = None
                self._condition.notify_all()

    def _wait_for(self, predicate: Callable[[], bool], timeout: float | None) -> None:
        end = None if timeout is None else time.monotonic() + timeout
        while not predicate():
            remaining = None if end is None else end - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise ProjectLockTimeout(f'Timed out acquiring {self.path}.')
            self._condition.wait(remaining)

    def _flock(self, operation: int, timeout: float | None) -> IO[bytes]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        if timeout is None:
            fcntl.flock(handle.fileno(), operation)
            return handle
        end = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
                return handle
            except BlockingIOError:
                if time.monotonic() >= end:
                    handle.close()
                    raise ProjectLockTimeout(f'Timed out acquiring {self.path}.') from None
                time.sleep(min(0.01, max(0.0, end - time.monotonic())))
