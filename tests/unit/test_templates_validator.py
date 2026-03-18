from __future__ import annotations

import json
from pathlib import Path

import pytest

import bulletjournal.templates.validator as validator
from bulletjournal.domain.enums import ArtifactRole, ValidationSeverity
from bulletjournal.domain.models import NotebookInterface, Port
from bulletjournal.parser.validation import build_issue


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload), encoding='utf-8')


def _interface(
    node_id: str,
    *,
    inputs: list[Port] | None = None,
    outputs: list[Port] | None = None,
    assets: list[Port] | None = None,
    issues=None,
) -> NotebookInterface:
    return NotebookInterface(
        node_id=node_id,
        source_hash='hash',
        inputs=inputs or [],
        outputs=outputs or [],
        assets=assets or [],
        issues=issues or [],
    )


def test_validate_template_handles_python_templates_and_unknown_suffix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    notebook = tmp_path / 'template.py'
    notebook.write_text('', encoding='utf-8')
    issue = build_issue(
        node_id='template',
        severity=ValidationSeverity.ERROR,
        code='bad_template',
        message='Broken template.',
    )

    monkeypatch.setattr(validator, 'parse_notebook_interface', lambda path, node_id: _interface(node_id, issues=[issue]))

    assert validator.validate_template(notebook) == [issue.to_dict()]

    unsupported = validator.validate_template(tmp_path / 'template.md')
    assert unsupported[0]['code'] == 'unsupported_template_type'


def test_validate_pipeline_template_reports_invalid_json(tmp_path: Path) -> None:
    template = tmp_path / 'pipeline.json'
    template.write_text('{bad json', encoding='utf-8')

    issues = validator.validate_pipeline_template(template)

    assert issues[0]['code'] == 'invalid_pipeline_template_json'
    assert issues[0]['details']['line'] == 1


def test_validate_pipeline_template_requires_object_root_and_lists(tmp_path: Path) -> None:
    non_object = tmp_path / 'non_object.json'
    missing_lists = tmp_path / 'missing_lists.json'
    _write_json(non_object, ['not', 'an', 'object'])
    _write_json(missing_lists, {'nodes': {}, 'edges': [], 'layout': []})

    root_issue = validator.validate_pipeline_template(non_object)
    shape_issue = validator.validate_pipeline_template(missing_lists)

    assert root_issue[0]['code'] == 'invalid_pipeline_template_shape'
    assert shape_issue[0]['code'] == 'invalid_pipeline_template_shape'
    assert 'nodes' in shape_issue[0]['message']


def test_pipeline_file_input_name_prefers_explicit_then_ui_then_default() -> None:
    assert validator._pipeline_file_input_name({'artifact_name': ' dataset '}) == 'dataset'
    assert validator._pipeline_file_input_name({'ui': {'artifact_name': ' upload '}}) == 'upload'
    assert validator._pipeline_file_input_name({}) == 'file'


def test_pipeline_node_interface_builds_file_input_output() -> None:
    interface = validator._pipeline_node_interface(
        {'id': 'upload', 'kind': 'file_input', 'ui': {'artifact_name': 'dataset'}},
        notebook_paths_by_ref={},
    )

    assert interface['inputs'] == []
    assert interface['assets'] == []
    assert interface['outputs'][0]['name'] == 'dataset'
    assert interface['outputs'][0]['data_type'] == 'file'
    assert interface['outputs'][0]['role'] == ArtifactRole.OUTPUT.value


def test_validate_pipeline_template_reports_missing_refs_and_layout_mismatches(tmp_path: Path) -> None:
    template = tmp_path / 'pipeline.json'
    _write_json(
        template,
        {
            'nodes': [
                {'id': 'consumer', 'title': 'Consumer', 'kind': 'notebook', 'template_ref': 'missing.py'},
            ],
            'edges': [],
            'layout': [{'node_id': 'ghost', 'x': 0, 'y': 0, 'w': 1, 'h': 1}],
        },
    )

    issues = validator.validate_pipeline_template(template, notebook_paths_by_ref={})
    codes = {issue['code'] for issue in issues}

    assert {'missing_template_ref', 'missing_pipeline_layout', 'unknown_layout_node'} <= codes


def test_validate_pipeline_template_reports_edge_port_and_type_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / 'source.py'
    target_path = tmp_path / 'target.py'
    template = tmp_path / 'pipeline.json'
    for path in (source_path, target_path):
        path.write_text('', encoding='utf-8')

    interfaces = {
        'source.py': _interface('source', outputs=[Port(name='out', data_type='int')]),
        'target.py': _interface('target', inputs=[Port(name='in', data_type='str', direction='input')]),
    }
    monkeypatch.setattr(validator, 'parse_notebook_interface', lambda path, node_id: interfaces[path.name])

    _write_json(
        template,
        {
            'nodes': [
                {'id': 'source', 'title': 'Source', 'kind': 'notebook', 'template_ref': 'source.py'},
                {'id': 'target', 'title': 'Target', 'kind': 'notebook', 'template_ref': 'target.py'},
            ],
            'edges': [
                {'source_node': 'source', 'source_port': 'missing', 'target_node': 'target', 'target_port': 'in'},
                {'source_node': 'source', 'source_port': 'out', 'target_node': 'target', 'target_port': 'missing'},
                {'source_node': 'source', 'source_port': 'out', 'target_node': 'target', 'target_port': 'in'},
            ],
            'layout': [
                {'node_id': 'source', 'x': 0, 'y': 0, 'w': 1, 'h': 1},
                {'node_id': 'target', 'x': 1, 'y': 0, 'w': 1, 'h': 1},
            ],
        },
    )

    issues = validator.validate_pipeline_template(
        template,
        notebook_paths_by_ref={'source.py': source_path, 'target.py': target_path},
    )
    codes = {issue['code'] for issue in issues}

    assert {'unknown_source_port', 'unknown_target_port', 'incompatible_edge_types'} <= codes


def test_validate_pipeline_template_accepts_valid_file_input_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    consumer_path = tmp_path / 'consumer.py'
    consumer_path.write_text('', encoding='utf-8')
    template = tmp_path / 'pipeline.json'

    monkeypatch.setattr(
        validator,
        'parse_notebook_interface',
        lambda path, node_id: _interface(
            node_id,
            inputs=[Port(name='dataset', data_type='file', kind='file', direction='input')],
        ),
    )

    _write_json(
        template,
        {
            'nodes': [
                {'id': 'upload', 'title': 'Upload', 'kind': 'file_input', 'artifact_name': 'dataset'},
                {'id': 'consumer', 'title': 'Consumer', 'kind': 'notebook', 'template_ref': 'consumer.py'},
            ],
            'edges': [
                {'source_node': 'upload', 'source_port': 'dataset', 'target_node': 'consumer', 'target_port': 'dataset'},
            ],
            'layout': [
                {'node_id': 'upload', 'x': 0, 'y': 0, 'w': 1, 'h': 1},
                {'node_id': 'consumer', 'x': 1, 'y': 0, 'w': 1, 'h': 1},
            ],
        },
    )

    assert validator.validate_pipeline_template(template, notebook_paths_by_ref={'consumer.py': consumer_path}) == []


def test_validate_pipeline_template_reports_graph_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node_a = tmp_path / 'a.py'
    node_b = tmp_path / 'b.py'
    template = tmp_path / 'pipeline.json'
    for path in (node_a, node_b):
        path.write_text('', encoding='utf-8')

    interfaces = {
        'a.py': _interface(
            'a',
            inputs=[Port(name='in', data_type='int', direction='input')],
            outputs=[Port(name='out', data_type='int')],
        ),
        'b.py': _interface(
            'b',
            inputs=[Port(name='in', data_type='int', direction='input')],
            outputs=[Port(name='out', data_type='int')],
        ),
    }
    monkeypatch.setattr(validator, 'parse_notebook_interface', lambda path, node_id: interfaces[path.name])

    _write_json(
        template,
        {
            'nodes': [
                {'id': 'a', 'title': 'A', 'kind': 'notebook', 'template_ref': 'a.py'},
                {'id': 'b', 'title': 'B', 'kind': 'notebook', 'template_ref': 'b.py'},
            ],
            'edges': [
                {'source_node': 'a', 'source_port': 'out', 'target_node': 'b', 'target_port': 'in'},
                {'source_node': 'b', 'source_port': 'out', 'target_node': 'a', 'target_port': 'in'},
            ],
            'layout': [
                {'node_id': 'a', 'x': 0, 'y': 0, 'w': 1, 'h': 1},
                {'node_id': 'b', 'x': 1, 'y': 0, 'w': 1, 'h': 1},
            ],
        },
    )

    issues = validator.validate_pipeline_template(template, notebook_paths_by_ref={'a.py': node_a, 'b.py': node_b})

    assert issues == [
        {
            'issue_id': issues[0]['issue_id'],
            'node_id': 'pipeline',
            'severity': 'error',
            'code': 'invalid_pipeline_graph',
            'message': 'graph must be acyclic',
            'details': {},
        }
    ]


def test_load_pipeline_template_definition_requires_object_root(tmp_path: Path) -> None:
    template = tmp_path / 'pipeline.json'
    _write_json(template, ['not', 'an', 'object'])

    with pytest.raises(ValueError, match='JSON object'):
        validator.load_pipeline_template_definition(template)
