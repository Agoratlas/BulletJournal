from pathlib import Path

from bulletjournal.domain.enums import ValidationSeverity
from bulletjournal.parser.docs_parser import extract_notebook_docs
from bulletjournal.parser.interface_parser import parse_notebook_interface


FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures'


def test_parser_extracts_interface_and_docs() -> None:
    notebook = FIXTURES / 'good_notebook.py'
    interface = parse_notebook_interface(notebook, node_id='good_notebook')

    assert [port.name for port in interface.inputs] == ['limit']
    assert [port.name for port in interface.outputs] == ['frame']
    assert [port.name for port in interface.assets] == ['summary']
    assert interface.outputs[0].data_type == 'pandas.DataFrame'
    assert interface.inputs[0].has_default is True
    assert extract_notebook_docs(notebook) == '# Notebook docs'


def test_parser_rejects_alias_calls() -> None:
    notebook = FIXTURES / 'bad_notebook_alias.py'
    interface = parse_notebook_interface(notebook, node_id='bad_notebook_alias')

    assert any(issue.severity == ValidationSeverity.ERROR for issue in interface.issues)
