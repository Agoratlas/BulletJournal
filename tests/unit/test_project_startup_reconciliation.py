from __future__ import annotations

from pathlib import Path

from bulletjournal.domain.enums import ArtifactRole, ArtifactState, LineageMode, NodeKind
from bulletjournal.domain.hashing import combine_hashes
from bulletjournal.domain.models import Node
from bulletjournal.parser.source_hash import normalized_source_hash_text
from bulletjournal.services.project_service import ProjectService
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.graph_store import GraphStore
from bulletjournal.storage.object_store import ObjectStore
from bulletjournal.storage.project_fs import ProjectPaths, init_project_root
from bulletjournal.storage.state_db import StateDB


class _Events:
    def publish(self, *args, **kwargs) -> None:
        _ = args, kwargs


SOURCE = """import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    artifacts.push('ok', name='result', data_type=str)
    return
"""


def _project_with_output(tmp_path: Path) -> tuple[Path, Node, StateDB, ObjectStore]:
    root = init_project_root(tmp_path / 'project').root
    paths = ProjectPaths(root)
    graph_store = GraphStore(paths)
    graph = graph_store.read()
    node = Node(id='book', kind=NodeKind.NOTEBOOK, title='Book', path='notebooks/book.py')
    graph.nodes.append(node)
    graph_store.write(graph)
    paths.notebook_path(node.id).write_text(SOURCE, encoding='utf-8')

    db = StateDB(paths.state_db_path)
    db.reconcile_node_incarnations([node])
    source_hash = normalized_source_hash_text(SOURCE)
    db.save_notebook_revision(
        node.id,
        source_hash,
        None,
        {
            'node_id': node.id,
            'source_hash': source_hash,
            'inputs': [],
            'outputs': [
                {
                    'name': 'result',
                    'data_type': 'str',
                    'role': 'output',
                    'kind': 'value',
                    'direction': 'output',
                }
            ],
            'issues': [],
        },
    )
    store = ObjectStore(paths)
    persisted = store.persist_value('ok', 'str')
    db.upsert_artifact_object(
        persisted['artifact_hash'],
        persisted['storage_kind'],
        persisted['data_type'],
        persisted['size_bytes'],
        persisted.get('extension'),
        persisted.get('mime_type'),
        persisted.get('preview'),
    )
    db.create_artifact_version(
        node_id=node.id,
        artifact_name='result',
        role=ArtifactRole.OUTPUT,
        artifact_hash=persisted['artifact_hash'],
        source_hash=source_hash,
        upstream_code_hash=combine_hashes([source_hash, 'book/result']),
        upstream_data_hash=combine_hashes([source_hash, 'book/result']),
        run_id='run-1',
        lineage_mode=LineageMode.MANAGED,
        warnings=[],
    )
    return root, node, db, store


def test_missing_startup_object_clears_head_and_project_opens(tmp_path: Path) -> None:
    root, node, db, store = _project_with_output(tmp_path)
    head = db.get_artifact_head(node.id, 'result')
    store.object_path(str(head['artifact_hash'])).unlink()

    snapshot = ProjectService(_Events(), TemplateService()).open_project(root)

    assert snapshot['project']['project_id']
    reconciled = StateDB(ProjectPaths(root).state_db_path).get_artifact_head(node.id, 'result')
    assert reconciled['current_version_id'] is None
    assert reconciled['state'] == ArtifactState.PENDING.value


def test_same_size_corruption_clears_head_and_quarantines_file(tmp_path: Path) -> None:
    root, node, db, store = _project_with_output(tmp_path)
    head = db.get_artifact_head(node.id, 'result')
    artifact_hash = str(head['artifact_hash'])
    path = store.object_path(artifact_hash)
    path.chmod(0o644)
    path.write_bytes(b'x' * path.stat().st_size)

    ProjectService(_Events(), TemplateService()).open_project(root)

    reconciled = StateDB(ProjectPaths(root).state_db_path).get_artifact_head(node.id, 'result')
    assert reconciled['current_version_id'] is None
    assert store.quarantine_path(artifact_hash).exists()


def test_startup_source_mismatch_marks_usable_output_stale(tmp_path: Path) -> None:
    root, node, _db, _store = _project_with_output(tmp_path)
    ProjectPaths(root).notebook_path(node.id).write_text(SOURCE + '\n# changed\n', encoding='utf-8')

    ProjectService(_Events(), TemplateService()).open_project(root)

    head = StateDB(ProjectPaths(root).state_db_path).get_artifact_head(node.id, 'result')
    assert head['current_version_id'] is not None
    assert head['state'] == ArtifactState.STALE.value


def test_startup_reconciliation_isolates_nodes_and_starts_watcher_last(tmp_path: Path, monkeypatch) -> None:
    root, node, db, store = _project_with_output(tmp_path)
    graph = GraphStore(ProjectPaths(root)).read()
    broken = Node(id='broken', kind=NodeKind.AREA, title='Broken')
    graph.nodes.append(broken)
    GraphStore(ProjectPaths(root)).write(graph)
    db.reconcile_node_incarnations([broken])
    store.object_path(str(db.get_artifact_head(node.id, 'result')['artifact_hash'])).unlink()
    calls: list[str] = []
    original = ProjectService._reconcile_startup_node

    def reconcile(self, item, current_graph):
        calls.append(item.id)
        if item.id == broken.id:
            raise RuntimeError('node failure')
        return original(self, item, current_graph)

    def start(_watcher) -> None:
        assert StateDB(ProjectPaths(root).state_db_path).get_artifact_head(node.id, 'result')['state'] == 'pending'
        calls.append('watcher')

    monkeypatch.setattr(ProjectService, '_reconcile_startup_node', reconcile)
    monkeypatch.setattr('bulletjournal.execution.watcher.NotebookWatcher.start', start)

    ProjectService(_Events(), TemplateService()).open_project(root)

    assert calls[-1] == 'watcher'
    assert {'book', 'broken'} <= set(calls)
    notices = StateDB(ProjectPaths(root).state_db_path).list_persistent_notices()
    assert any(notice['code'] == 'startup_reconciliation_failed' for notice in notices)
