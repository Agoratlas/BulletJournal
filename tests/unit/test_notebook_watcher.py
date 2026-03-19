from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bulletjournal.execution.watcher import NotebookWatcher


def test_notebook_watcher_uses_bound_project_and_reparses_changed_files(tmp_path: Path) -> None:
    notebook_path = tmp_path / 'notebooks' / 'demo.py'
    notebook_path.parent.mkdir(parents=True)
    notebook_path.write_text('print(1)\n', encoding='utf-8')
    reparsed: list[Path] = []
    service = SimpleNamespace(
        project=SimpleNamespace(paths=SimpleNamespace(notebooks_dir=notebook_path.parent)),
        reparse_notebook_by_path=lambda path: reparsed.append(path),
    )
    watcher = NotebookWatcher(service)

    watcher._scan()
    notebook_path.write_text('print(2)\n', encoding='utf-8')
    watcher._scan()

    assert reparsed == [notebook_path]
