from pathlib import Path

from bulletjournal.domain.enums import ValidationSeverity
from bulletjournal.parser.docs_parser import extract_notebook_docs
from bulletjournal.parser.interface_parser import parse_notebook_contract, parse_notebook_interface

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


def test_parser_extracts_asset_declarations_in_same_pass(tmp_path) -> None:
    notebook = tmp_path / 'asset_notebook.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts, assets

@app.cell
def _():
    value = artifacts.pull(name='dataset', data_type=int)
    frame_asset = assets.DataFrame(__import__('pandas').DataFrame({'value': [value]}))
    assets.push(frame_asset, name='table', title='Table', description='Rows', asset_type=assets.DataFrame)
    assets.push(assets.Markdown('hello'), name='notes', title='Notes')
    artifacts.push(value, name='result', data_type=int)
    return value
""".strip()
        + '\n',
        encoding='utf-8',
    )

    contract = parse_notebook_contract(notebook, node_id='asset_notebook')

    assert [port.name for port in contract.interface.inputs] == ['dataset']
    assert [port.name for port in contract.interface.outputs] == ['result']
    assert [declaration.name for declaration in contract.asset_declarations] == ['table', 'notes']
    assert contract.asset_declarations[0].declared_asset_type == 'dataframe'
    assert contract.asset_declarations[1].declared_asset_type is None


def test_parser_rejects_non_literal_asset_metadata_and_invalid_asset_type(tmp_path) -> None:
    notebook = tmp_path / 'invalid_asset_notebook.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

TITLE = 'Dynamic'

@app.cell
def _():
    note = assets.Markdown('hello')
    assets.push(note, name='notes', title=TITLE, asset_type=TITLE)
    return note
""".strip()
        + '\n',
        encoding='utf-8',
    )

    contract = parse_notebook_contract(notebook, node_id='invalid_asset_notebook')

    assert any(issue.code == 'invalid_title' for issue in contract.issues)
    assert any(issue.code == 'invalid_asset_type' for issue in contract.issues)


def test_parser_accepts_histogram_asset_type_reference(tmp_path) -> None:
    notebook = tmp_path / 'histogram_asset_notebook.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _(pd=__import__('pandas')):
    frame = pd.DataFrame({'value': [1, 2, 3]})
    assets.push(assets.Histogram(frame, x='value', bins=12), name='value_hist', title='Value histogram', asset_type=assets.Histogram)
    return frame
""".strip()
        + '\n',
        encoding='utf-8',
    )

    contract = parse_notebook_contract(notebook, node_id='histogram_asset_notebook')

    assert [declaration.name for declaration in contract.asset_declarations] == ['value_hist']
    assert contract.asset_declarations[0].declared_asset_type == 'histogram'
    assert not any(issue.severity == ValidationSeverity.ERROR for issue in contract.issues)


def test_parser_accepts_time_histogram_asset_type_reference(tmp_path) -> None:
    notebook = tmp_path / 'time_histogram_asset_notebook.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _(pd=__import__('pandas')):
    frame = pd.DataFrame({'created_at': pd.date_range('2024-01-01', periods=3, freq='D')})
    assets.push(assets.TimeHistogram(frame, x='created_at'), name='created_hist', title='Created histogram', asset_type=assets.TimeHistogram)
    return frame
""".strip()
        + '\n',
        encoding='utf-8',
    )

    contract = parse_notebook_contract(notebook, node_id='time_histogram_asset_notebook')

    assert [declaration.name for declaration in contract.asset_declarations] == ['created_hist']
    assert contract.asset_declarations[0].declared_asset_type == 'time_histogram'
    assert not any(issue.severity == ValidationSeverity.ERROR for issue in contract.issues)


def test_parser_accepts_iframe_asset_type_reference(tmp_path) -> None:
    notebook = tmp_path / 'iframe_asset_notebook.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _():
    assets.push(assets.Iframe('https://example.com/embed'), name='embedded_report', title='Embedded report', asset_type=assets.Iframe)
    return
""".strip()
        + '\n',
        encoding='utf-8',
    )

    contract = parse_notebook_contract(notebook, node_id='iframe_asset_notebook')

    assert [declaration.name for declaration in contract.asset_declarations] == ['embedded_report']
    assert contract.asset_declarations[0].declared_asset_type == 'iframe'
    assert not any(issue.severity == ValidationSeverity.ERROR for issue in contract.issues)


def test_parser_accepts_scatter_plot_asset_type_reference(tmp_path) -> None:
    notebook = tmp_path / 'scatter_plot_asset_notebook.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _(pd=__import__('pandas')):
    frame = pd.DataFrame({'x': [1, 2, 3], 'y': [4, 5, 6]})
    assets.push(assets.ScatterPlot(frame, x='x', y='y'), name='xy_plot', title='XY plot', asset_type=assets.ScatterPlot)
    return frame
""".strip()
        + '\n',
        encoding='utf-8',
    )

    contract = parse_notebook_contract(notebook, node_id='scatter_plot_asset_notebook')

    assert [declaration.name for declaration in contract.asset_declarations] == ['xy_plot']
    assert contract.asset_declarations[0].declared_asset_type == 'scatter_plot'
    assert not any(issue.severity == ValidationSeverity.ERROR for issue in contract.issues)


def test_parser_accepts_pie_chart_asset_type_reference(tmp_path) -> None:
    notebook = tmp_path / 'pie_chart_asset_notebook.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _(pd=__import__('pandas')):
    frame = pd.DataFrame({'category': ['a', 'b', 'a']})
    assets.push(assets.PieChart(frame, category='category'), name='category_share', title='Category share', asset_type=assets.PieChart)
    return frame
""".strip()
        + '\n',
        encoding='utf-8',
    )

    contract = parse_notebook_contract(notebook, node_id='pie_chart_asset_notebook')

    assert [declaration.name for declaration in contract.asset_declarations] == ['category_share']
    assert contract.asset_declarations[0].declared_asset_type == 'pie_chart'
    assert not any(issue.severity == ValidationSeverity.ERROR for issue in contract.issues)


def test_parser_accepts_bar_chart_asset_type_reference(tmp_path) -> None:
    notebook = tmp_path / 'bar_chart_asset_notebook.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _(pd=__import__('pandas')):
    frame = pd.DataFrame({'category': ['a', 'b', 'a'], 'value': [1, 2, 3]})
    assets.push(assets.BarChart(frame, category='category', value='value'), name='category_totals', title='Category totals', asset_type=assets.BarChart)
    return frame
""".strip()
        + '\n',
        encoding='utf-8',
    )

    contract = parse_notebook_contract(notebook, node_id='bar_chart_asset_notebook')

    assert [declaration.name for declaration in contract.asset_declarations] == ['category_totals']
    assert contract.asset_declarations[0].declared_asset_type == 'bar_chart'
    assert not any(issue.severity == ValidationSeverity.ERROR for issue in contract.issues)


def test_parser_accepts_collection_asset_type_reference(tmp_path) -> None:
    notebook = tmp_path / 'collection_asset_notebook.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _():
    coll = assets.Collection(display_mode='single')
    coll.add_asset(assets.Markdown('hello'))
    assets.push(coll, name='notes_collection', title='Notes collection', asset_type=assets.Collection)
    return coll
""".strip()
        + '\n',
        encoding='utf-8',
    )

    contract = parse_notebook_contract(notebook, node_id='collection_asset_notebook')

    assert [declaration.name for declaration in contract.asset_declarations] == ['notes_collection']
    assert contract.asset_declarations[0].declared_asset_type == 'collection'
    assert not any(issue.severity == ValidationSeverity.ERROR for issue in contract.issues)


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


def test_parser_rejects_invalid_asset_names(tmp_path) -> None:
    notebook = tmp_path / 'invalid_asset_names.py'
    notebook.write_text(
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import assets

@app.cell
def _():
    assets.push(assets.Markdown('hello'), name='bad-name', title='Notes')
    return
""".strip()
        + '\n',
        encoding='utf-8',
    )

    interface = parse_notebook_interface(notebook, node_id='invalid_asset_names')

    invalid_name_issues = [issue for issue in interface.issues if issue.code == 'invalid_name']
    assert len(invalid_name_issues) == 1
    assert invalid_name_issues[0].message == (
        'Invalid artifact name `bad-name`, must only contain lowercase letters, digits and underscores.'
    )


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
