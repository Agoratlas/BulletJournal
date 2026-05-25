from pathlib import Path

from bulletjournal.domain.enums import ValidationSeverity
from bulletjournal.parser.docs_parser import extract_notebook_docs
from bulletjournal.parser.interface_parser import parse_notebook_interface

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures'


def test_parser_extracts_interface_and_docs() -> None:
    notebook = FIXTURES / 'good_notebook.py'
    interface = parse_notebook_interface(notebook, node_id='good_notebook')

    assert [port.name for port in interface.inputs] == ['limit']
    assert [port.name for port in interface.outputs] == ['frame', 'summary']
    assert interface.outputs[0].data_type == 'pandas.DataFrame'
    assert interface.inputs[0].has_default is True
    assert extract_notebook_docs(notebook) == '# Notebook docs'


def test_parser_marks_pull_file_allow_missing_as_optional(tmp_path) -> None:
    notebook = tmp_path / 'optional_file.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    dataset = artifacts.pull_file(name='dataset', allow_missing=True)
    return dataset
""".strip()
        + '\n',
        encoding='utf-8',
    )

    interface = parse_notebook_interface(notebook, node_id='optional_file')

    assert interface.inputs[0].name == 'dataset'
    assert interface.inputs[0].data_type == 'file'
    assert interface.inputs[0].has_default is True
    assert interface.inputs[0].default is None


def test_parser_preserves_port_declaration_order(tmp_path) -> None:
    notebook = tmp_path / 'port_order.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    second = artifacts.pull(name='second', data_type=int)
    first = artifacts.pull(name='first', data_type=int)
    return second, first

@app.cell
def _(second, first):
    artifacts.push(second, name='zeta', data_type=int)
    artifacts.push(first, name='alpha', data_type=int)
    artifacts.push('notes', name='later_asset', data_type=str)
    artifacts.push('summary', name='earlier_asset', data_type=str)
    return
""".strip()
        + '\n',
        encoding='utf-8',
    )

    interface = parse_notebook_interface(notebook, node_id='port_order')

    assert [port.name for port in interface.inputs] == ['second', 'first']
    assert [port.name for port in interface.outputs] == ['zeta', 'alpha', 'later_asset', 'earlier_asset']


def test_parser_allows_matching_input_and_output_names(tmp_path) -> None:
    notebook = tmp_path / 'same_name.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    value = artifacts.pull(name='dataset', data_type=int)
    artifacts.push(value, name='dataset', data_type=int)
    return value
""".strip()
        + '\n',
        encoding='utf-8',
    )

    interface = parse_notebook_interface(notebook, node_id='same_name')

    assert [port.name for port in interface.inputs] == ['dataset']
    assert [port.name for port in interface.outputs] == ['dataset']
    assert not any(issue.code == 'duplicate_port' for issue in interface.issues)


def test_parser_rejects_invalid_artifact_names(tmp_path) -> None:
    notebook = tmp_path / 'invalid_names.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

@app.cell
def _():
    value = artifacts.pull(name='bad-name', data_type=int)
    artifacts.push(value, name='also-bad', data_type=int)
    return value
""".strip()
        + '\n',
        encoding='utf-8',
    )

    interface = parse_notebook_interface(notebook, node_id='invalid_names')

    invalid_name_issues = [issue for issue in interface.issues if issue.code == 'invalid_name']
    assert len(invalid_name_issues) == 2
    assert {issue.message for issue in invalid_name_issues} == {
        'Invalid artifact name `bad-name`, must only contain lowercase letters, digits and underscores.',
        'Invalid artifact name `also-bad`, must only contain lowercase letters, digits and underscores.',
    }


def test_parser_rejects_non_literal_pull_file_allow_missing(tmp_path) -> None:
    notebook = tmp_path / 'invalid_optional_file.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts

FLAG = True

@app.cell
def _():
    dataset = artifacts.pull_file(name='dataset', allow_missing=FLAG)
    return dataset
""".strip()
        + '\n',
        encoding='utf-8',
    )

    interface = parse_notebook_interface(notebook, node_id='invalid_optional_file')

    assert any(issue.code == 'invalid_allow_missing' for issue in interface.issues)


def test_parser_rejects_alias_calls() -> None:
    notebook = FIXTURES / 'bad_notebook_alias.py'
    interface = parse_notebook_interface(notebook, node_id='bad_notebook_alias')

    assert any(issue.severity == ValidationSeverity.ERROR for issue in interface.issues)


def test_parser_reports_duplicate_cell_globals() -> None:
    notebook = FIXTURES / 'bad_notebook_duplicate_globals.py'
    interface = parse_notebook_interface(notebook, node_id='bad_notebook_duplicate_globals')

    assert any(issue.code == 'duplicate_cell_global' for issue in interface.issues)


def test_parser_reports_syntax_errors() -> None:
    notebook = FIXTURES / 'bad_notebook_syntax.py'
    interface = parse_notebook_interface(notebook, node_id='bad_notebook_syntax')

    assert any(issue.code == 'invalid_syntax' for issue in interface.issues)


def test_parser_reports_unparsable_marimo_cells() -> None:
    notebook = FIXTURES / 'bad_notebook_unparsable_cell.py'
    interface = parse_notebook_interface(notebook, node_id='bad_notebook_unparsable_cell')

    assert any(issue.code == 'invalid_syntax' for issue in interface.issues)
