import ast

import pytest

from bulletjournal.domain.errors import GraphValidationError
from bulletjournal.services.template_service import TemplateService
from bulletjournal.templates.notebook_draft import render_notebook_inputs


def scaffold():
    return TemplateService().render_resolved_notebook_template_source('builtin/empty_notebook', node_id='draft')


def test_inputs_keep_order_and_escape_documentation_without_activating_output_example():
    documentation = 'Quotes: " and \'\nUnicode: café\n    artifacts.push("not code")'
    source = render_notebook_inputs(
        scaffold(),
        [
            {'name': 'second', 'data_type': 'pandas.DataFrame', 'description': documentation},
            {'name': 'first', 'data_type': 'int', 'default_to_none': True},
            {'name': 'file', 'data_type': 'file', 'default_to_none': True},
        ],
    )
    module = ast.parse(source)
    calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == 'artifacts'
    ]
    assert [call.func.attr for call in calls] == ['pull', 'pull', 'pull_file']
    params = [{param.arg: param.value for param in call.keywords} for call in calls]
    assert [ast.literal_eval(param['name']) for param in params] == ['second', 'first', 'file']
    assert ast.literal_eval(params[0]['description']) == documentation
    assert 'default' not in params[0]
    assert ast.literal_eval(params[1]['default']) is None
    assert ast.literal_eval(params[2]['allow_missing']) is True
    assert source.index("name='second'") < source.index("name='first'") < source.index('    # artifacts.push(')
    assert 'second = artifacts.pull(\n        name=' in source


def test_input_variables_are_valid_unique_and_do_not_shadow_setup_or_builtin_types():
    names = ['pd', 'artifacts', 'mo', 'assets', 'class', '123', '_', '__', 'a', '_a', 'int', 'list']
    source = render_notebook_inputs(scaffold(), [{'name': name, 'data_type': 'int'} for name in names])
    module = ast.parse(source)
    variables = [
        statement.targets[0].id
        for cell in module.body
        if isinstance(cell, ast.FunctionDef)
        for statement in cell.body
        if isinstance(statement, ast.Assign)
    ]
    assert len(variables) == len(names)
    assert len(set(variables)) == len(names)
    assert not {'pd', 'artifacts', 'mo', 'assets', 'int', 'list'} & set(variables)


@pytest.mark.parametrize(
    'inputs',
    [
        [{'name': 'BAD', 'data_type': 'int'}],
        [{'name': 'a', 'data_type': 'int'}, {'name': 'a', 'data_type': 'int'}],
        [{'name': 'a', 'data_type': '__import__("os")'}],
    ],
)
def test_invalid_input_definitions_are_rejected(inputs):
    with pytest.raises(GraphValidationError):
        render_notebook_inputs(scaffold(), inputs)


def test_empty_input_list_leaves_only_documentation_and_commented_output_example():
    source = render_notebook_inputs(scaffold(), [])
    assert '# Notebook inputs are inserted here.' not in source
    assert 'artifacts.pull(' not in source
    assert '    # artifacts.push(' in source
