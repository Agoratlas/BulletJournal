from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from bulletjournal.config import WATCH_INTERVAL_SECONDS
from bulletjournal.parser.source_hash import compute_source_hash


logger = logging.getLogger(__name__)


class NotebookWatcher:
    def __init__(self, project_service) -> None:
        self.project_service = project_service
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._file_state: dict[str, tuple[float, str]] = {}

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
                logger.exception('Notebook watcher scan failed.')
            time.sleep(WATCH_INTERVAL_SECONDS)

    def _scan(self) -> None:
        project = self.project_service.project
        if project is None:
            self._file_state.clear()
            return
        current_paths = {str(path) for path in project.paths.notebooks_dir.glob('*.py')}
        self._file_state = {key: value for key, value in self._file_state.items() if key in current_paths}
        for notebook_path in sorted(project.paths.notebooks_dir.glob('*.py')):
            mtime = notebook_path.stat().st_mtime
            key = str(notebook_path)
            previous = self._file_state.get(key)
            source_hash = compute_source_hash(notebook_path)
            if previous is None:
                self._file_state[key] = (mtime, source_hash)
                continue
            previous_mtime, previous_hash = previous
            if mtime != previous_mtime:
                self._file_state[key] = (mtime, source_hash)
                if source_hash == previous_hash:
                    continue
                self.project_service.reparse_notebook_by_path(Path(key))
