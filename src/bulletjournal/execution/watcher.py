from __future__ import annotations

import threading
import time
from pathlib import Path

from bulletjournal.config import WATCH_INTERVAL_SECONDS


class NotebookWatcher:
    def __init__(self, project_service) -> None:
        self.project_service = project_service
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._mtimes: dict[str, float] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='bulletjournal-notebook-watcher', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(WATCH_INTERVAL_SECONDS)

    def _scan(self) -> None:
        project = self.project_service.current_project
        if project is None:
            return
        for notebook_path in sorted(project.paths.notebooks_dir.glob('*.py')):
            mtime = notebook_path.stat().st_mtime
            key = str(notebook_path)
            previous = self._mtimes.get(key)
            if previous is None:
                self._mtimes[key] = mtime
                continue
            if mtime != previous:
                self._mtimes[key] = mtime
                self.project_service.reparse_notebook_by_path(Path(key))
