from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event

from bulletjournal.execution.manifests import RunManifest
from bulletjournal.storage.atomic_write import atomic_write_text


class WorkerRunner:
    def run(
        self,
        manifest: RunManifest,
        *,
        temp_dir: Path,
        cancel_event: Event | None = None,
        on_process_started: Callable[[subprocess.Popen], None] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        temp_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = temp_dir / f'{manifest.run_id}_{manifest.node_id}.json'
        progress_path = temp_dir / f'{manifest.run_id}_{manifest.node_id}.progress.json'
        manifest.progress_path = str(progress_path)
        atomic_write_text(manifest_path, json.dumps(manifest.to_dict(), sort_keys=True))
        process = subprocess.Popen(
            [sys.executable, '-m', 'bulletjournal.execution.worker_main', str(manifest_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if on_process_started is not None:
            on_process_started(process)
        progress_state: dict[str, object] | None = None
        while process.poll() is None:
            if progress_path.exists():
                try:
                    progress_state = json.loads(progress_path.read_text(encoding='utf-8'))
                    if on_progress is not None and progress_state is not None:
                        on_progress(progress_state)
                except json.JSONDecodeError:
                    pass
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                stdout, stderr = process.communicate(timeout=5)
                return {
                    'status': 'cancelled',
                    'outputs': [],
                    'stderr': stderr,
                    'stdout': stdout,
                    'returncode': process.returncode,
                    'progress': progress_state,
                }
            time.sleep(0.1)
        stdout, stderr = process.communicate()
        stdout = stdout.strip() or '{}'
        payload = json.loads(stdout)
        payload['returncode'] = process.returncode
        if progress_state is not None:
            payload['progress'] = progress_state
        if stderr.strip():
            payload['stderr'] = stderr
        return payload
