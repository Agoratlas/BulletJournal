from __future__ import annotations

import pytest

from bulletjournal.domain.errors import InvalidRequestError
from bulletjournal.services.graph_service import GraphService
from bulletjournal.services.notebook_service import NotebookService
from bulletjournal.services.project_service import ProjectService
from bulletjournal.services.template_service import TemplateService
from bulletjournal.storage.project_fs import init_project_root


class _FakeEventService:
    def publish(self, *args, **kwargs) -> None:
        pass


def _notebook_service(tmp_path) -> tuple[ProjectService, NotebookService]:
    project_service = ProjectService(_FakeEventService(), TemplateService())
    project_service.open_project(init_project_root(tmp_path / 'project').root)
    GraphService(project_service).apply_operations(
        int(project_service.graph().meta['graph_version']),
        [{'type': 'add_notebook_node', 'node_id': 'sample_node', 'title': 'Sample node'}],
    )
    return project_service, NotebookService(project_service)


def test_notebook_source_can_be_read_and_replaced(tmp_path) -> None:
    _, service = _notebook_service(tmp_path)

    before = service.get_notebook_source('sample_node')
    result = service.update_notebook_source('sample_node', before['source_text'].replace('Example', 'Updated example'))

    assert result['node_id'] == 'sample_node'
    assert service.get_notebook_source('sample_node')['source_text'].startswith('import marimo')


def test_notebook_source_can_be_read_by_line_range(tmp_path) -> None:
    _, service = _notebook_service(tmp_path)
    service.update_notebook_source('sample_node', 'one\ntwo\nthree\n')

    page = service.get_notebook_source('sample_node', offset=1, limit=1)
    final_page = service.get_notebook_source('sample_node', offset=3, limit=1)

    assert page == {
        'node_id': 'sample_node',
        'source_text': 'two\n',
        'offset': 1,
        'limit': 1,
        'total_lines': 3,
        'returned_lines': 1,
        'next_offset': 2,
    }
    assert final_page['source_text'] == ''
    assert final_page['returned_lines'] == 0
    assert final_page['next_offset'] is None


def test_notebook_source_range_rejects_invalid_bounds(tmp_path) -> None:
    _, service = _notebook_service(tmp_path)

    with pytest.raises(InvalidRequestError, match='non-negative'):
        service.get_notebook_source('sample_node', offset=-1)
    with pytest.raises(InvalidRequestError, match='between 1 and 100'):
        service.get_notebook_source('sample_node', limit=101)
    with pytest.raises(InvalidRequestError, match='beyond'):
        service.get_notebook_source('sample_node', offset=10_000)


def test_notebook_source_patch_replaces_an_inclusive_line_range(tmp_path) -> None:
    _, service = _notebook_service(tmp_path)
    service.update_notebook_source('sample_node', 'one\ntwo\nthree\n')

    result = service.patch_notebook_source(
        'sample_node', start_line=2, end_line=3, replacement='second\nthird\nfourth\n'
    )

    assert result['start_line'] == 2
    assert result['end_line'] == 3
    assert result['line_count'] == 4
    assert service.get_notebook_source('sample_node')['source_text'] == 'one\nsecond\nthird\nfourth\n'


def test_notebook_source_patch_validates_range_and_replacement(tmp_path) -> None:
    _, service = _notebook_service(tmp_path)
    service.update_notebook_source('sample_node', 'one\n')

    with pytest.raises(InvalidRequestError, match='one-based positive'):
        service.patch_notebook_source('sample_node', start_line=0, end_line=1, replacement='x')
    with pytest.raises(InvalidRequestError, match='greater than or equal'):
        service.patch_notebook_source('sample_node', start_line=2, end_line=1, replacement='x')
    with pytest.raises(InvalidRequestError, match='must not exceed'):
        service.patch_notebook_source('sample_node', start_line=1, end_line=2, replacement='x')
    with pytest.raises(InvalidRequestError, match='must be a string'):
        service.patch_notebook_source('sample_node', start_line=1, end_line=1, replacement=1)  # type: ignore[arg-type]


def test_notebook_source_rejects_non_notebook_nodes(tmp_path) -> None:
    project_service, service = _notebook_service(tmp_path)
    GraphService(project_service).apply_operations(
        int(project_service.graph().meta['graph_version']),
        [{'type': 'add_constant_node', 'node_id': 'constant', 'data_type': 'str'}],
    )

    with pytest.raises(InvalidRequestError, match='not a notebook'):
        service.get_notebook_source('constant')
