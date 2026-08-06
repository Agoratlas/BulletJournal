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
        result_path = temp_dir / f'{manifest.run_id}_{manifest.node_id}.result.json'
        stdout_path = (
            Path(manifest.stdout_path)
            if manifest.stdout_path
            else temp_dir / f'{manifest.run_id}_{manifest.node_id}.stdout.log'
        )
        stderr_path = (
            Path(manifest.stderr_path)
            if manifest.stderr_path
            else temp_dir / f'{manifest.run_id}_{manifest.node_id}.stderr.log'
        )
        manifest.progress_path = str(progress_path)
        manifest.stdout_path = str(stdout_path)
        manifest.stderr_path = str(stderr_path)
        manifest.result_path = str(result_path)
        atomic_write_text(manifest_path, json.dumps(manifest.to_dict(), sort_keys=True))
        result_path.unlink(missing_ok=True)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        with (
            stdout_path.open('w', encoding='utf-8', buffering=1) as stdout_log,
            stderr_path.open('w', encoding='utf-8', buffering=1) as stderr_log,
        ):
            process = subprocess.Popen(  # noqa: S603
                [sys.executable, '-m', 'bulletjournal.execution.worker_main', str(manifest_path)],
                stdout=stdout_log,
                stderr=stderr_log,
                text=True,
            )
            if on_process_started is not None:
                on_process_started(process)
            progress_state: dict[str, object] | None = None
            published_progress_state: dict[str, object] | None = None
            cancelled = False
            while process.poll() is None:
                if progress_path.exists():
                    try:
                        progress_state = json.loads(progress_path.read_text(encoding='utf-8'))
                        if (
                            on_progress is not None
                            and progress_state is not None
                            and progress_state != published_progress_state
                        ):
                            on_progress(progress_state)
                            published_progress_state = progress_state
                    except json.JSONDecodeError:
                        pass
                if cancel_event is not None and cancel_event.is_set():
                    process.terminate()
                    process.wait(timeout=5)
                    cancelled = True
                    break
                time.sleep(0.1)
            if not cancelled:
                process.wait()
        if cancelled:
            return _cancelled_payload(
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                returncode=process.returncode,
                progress=progress_state,
            )
        try:
            payload = json.loads(result_path.read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError):
            payload = {
                'status': 'error',
                'error': _summarize_worker_failure(
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    returncode=process.returncode,
                ),
                'outputs': [],
            }
        payload['returncode'] = process.returncode
        if progress_state is not None:
            payload['progress'] = progress_state
        if stdout_path.exists():
            payload['stdout'] = stdout_path.read_text(encoding='utf-8')
        if stderr_path.exists():
            payload['stderr'] = stderr_path.read_text(encoding='utf-8')
        return payload


def _cancelled_payload(
    *,
    stdout_path: Path,
    stderr_path: Path,
    returncode: int | None,
    progress: dict[str, object] | None,
) -> dict[str, object]:
    return {
        'status': 'cancelled',
        'outputs': [],
        'stdout': _read_log(stdout_path),
        'stderr': _read_log(stderr_path),
        'returncode': returncode,
        'progress': progress,
    }


def _summarize_worker_failure(*, stdout_path: Path, stderr_path: Path, returncode: int | None) -> str:
    for text in (_read_log(stderr_path), _read_log(stdout_path)):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            return lines[-1]
    if returncode is None:
        return 'Worker exited without producing a valid result file.'
    return f'Worker exited with code {returncode} without producing a valid result file.'


def _read_log(path: Path) -> str:
    return path.read_text(encoding='utf-8') if path.exists() else ''
