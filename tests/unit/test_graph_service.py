from __future__ import annotations

from types import SimpleNamespace

import pytest

from bulletjournal.domain.errors import GraphValidationError
from bulletjournal.services.graph_service import GraphService
from bulletjournal.services.project_service import ProjectService
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_fs import init_project_root


class _FakeEventService:
    def publish(self, *args, **kwargs) -> None:
        _ = (args, kwargs)


def _consumer_source() -> str:
    return (
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    sample_count = artifacts.pull(name='sample_count', data_type=int)
    return sample_count

@app.cell
def _(sample_count):
    artifacts.push(sample_count * 2, name='doubled', data_type=int)
    return

if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip()
        + '\n'
    )


def test_graph_service_restores_deleted_notebook_and_edges_in_same_request(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'source',
                'title': 'Source',
                'template_ref': 'builtin/value_input',
            },
            {
                'type': 'add_notebook_node',
                'node_id': 'consumer',
                'title': 'Consumer',
                'source_text': _consumer_source(),
                'x': 480,
                'y': 80,
            },
        ],
    )

    connected = graph_service.apply_operations(
        int(created['meta']['graph_version']),
        [
            {
                'type': 'add_edge',
                'source_node': 'source',
                'source_port': 'value',
                'target_node': 'consumer',
                'target_port': 'sample_count',
            }
        ],
    )

    deleted = graph_service.apply_operations(
        int(connected['meta']['graph_version']),
        [
            {
                'type': 'delete_node',
                'node_id': 'consumer',
            }
        ],
    )

    restored = graph_service.apply_operations(
        int(deleted['meta']['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'consumer',
                'title': 'Consumer',
                'source_text': _consumer_source(),
                'x': 480,
                'y': 80,
            },
            {
                'type': 'add_edge',
                'source_node': 'source',
                'source_port': 'value',
                'target_node': 'consumer',
                'target_port': 'sample_count',
            },
        ],
    )

    assert any(
        edge['source_node'] == 'source'
        and edge['source_port'] == 'value'
        and edge['target_node'] == 'consumer'
        and edge['target_port'] == 'sample_count'
        for edge in restored['edges']
    )
    snapshot = project_service.snapshot()
    consumer = next(node for node in snapshot['graph']['nodes'] if node['id'] == 'consumer')
    assert consumer['interface'] is not None
    assert [port['name'] for port in consumer['interface']['inputs']] == ['sample_count']


def test_graph_service_renames_notebook_app_title_with_new_node_id(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
                'template_ref': 'builtin/test_starter_notebook',
            }
        ],
    )

    renamed = graph_service.apply_operations(
        int(created['meta']['graph_version']),
        [
            {
                'type': 'rename_node',
                'node_id': 'sample_node',
                'new_node_id': 'renamed_node',
                'title': 'Sample Node',
            }
        ],
    )

    source = (project_root / 'notebooks' / 'renamed_node.py').read_text(encoding='utf-8')
    assert "app_title='renamed_node'" in source
    assert not (project_root / 'notebooks' / 'sample_node.py').exists()
    assert any(node['id'] == 'renamed_node' for node in renamed['nodes'])


def test_graph_service_blocks_notebook_id_change_while_editor_is_open(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
            }
        ],
    )

    project_service.run_service = SimpleNamespace(
        session_manager=SimpleNamespace(get_by_node=lambda node_id: object() if node_id == 'sample_node' else None),
        orchestrator_state=lambda: {},
    )

    with pytest.raises(GraphValidationError, match='editor is open'):
        graph_service.apply_operations(
            int(created['meta']['graph_version']),
            [
                {
                    'type': 'rename_node',
                    'node_id': 'sample_node',
                    'new_node_id': 'renamed_sample',
                    'title': 'Renamed sample',
                }
            ],
        )

    updated = graph_service.apply_operations(
        int(created['meta']['graph_version']),
        [
            {
                'type': 'update_node_title',
                'node_id': 'sample_node',
                'title': 'Renamed Sample',
            }
        ],
    )

    assert any(node['id'] == 'sample_node' and node['title'] == 'Renamed Sample' for node in updated['nodes'])


def test_graph_service_blocks_notebook_id_change_while_queued(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
            }
        ],
    )

    project_service.run_service = SimpleNamespace(
        session_manager=SimpleNamespace(get_by_node=lambda _node_id: None),
        orchestrator_state=lambda: {'sample_node': {'status': 'queued'}},
    )

    with pytest.raises(GraphValidationError, match='queued for execution'):
        graph_service.apply_operations(
            int(created['meta']['graph_version']),
            [
                {
                    'type': 'rename_node',
                    'node_id': 'sample_node',
                    'new_node_id': 'renamed_sample',
                    'title': 'Sample Node',
                }
            ],
        )

    updated = graph_service.apply_operations(
        int(created['meta']['graph_version']),
        [
            {
                'type': 'update_node_title',
                'node_id': 'sample_node',
                'title': 'Renamed Sample',
            }
        ],
    )

    assert any(node['id'] == 'sample_node' and node['title'] == 'Renamed Sample' for node in updated['nodes'])


def test_graph_service_blocks_notebook_id_change_while_running(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(project_root)
    graph_service = GraphService(project_service)

    created = graph_service.apply_operations(
        int(project_service.graph().meta['graph_version']),
        [
            {
                'type': 'add_notebook_node',
                'node_id': 'sample_node',
                'title': 'Sample Node',
            }
        ],
    )

    project_service.run_service = SimpleNamespace(
        session_manager=SimpleNamespace(get_by_node=lambda _node_id: None),
        orchestrator_state=lambda: {'sample_node': {'status': 'running'}},
    )

    with pytest.raises(GraphValidationError, match='while it is running'):
        graph_service.apply_operations(
            int(created['meta']['graph_version']),
            [
                {
                    'type': 'rename_node',
                    'node_id': 'sample_node',
                    'new_node_id': 'renamed_sample',
                    'title': 'Sample Node',
                }
            ],
        )

    updated = graph_service.apply_operations(
        int(created['meta']['graph_version']),
        [
            {
                'type': 'update_node_title',
                'node_id': 'sample_node',
                'title': 'Renamed Sample',
            }
        ],
    )

    assert any(node['id'] == 'sample_node' and node['title'] == 'Renamed Sample' for node in updated['nodes'])
