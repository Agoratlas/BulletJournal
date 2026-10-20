from __future__ import annotations

import keyword
import re
from typing import Any

from bulletjournal.domain.artifact_names import is_valid_artifact_name
from bulletjournal.domain.errors import GraphValidationError
from bulletjournal.domain.type_system import CANONICAL_TYPES

INPUT_MARKER = '# Notebook inputs are inserted here.'


def render_notebook_inputs(source: str, inputs: list[dict[str, Any]]) -> str:
    """Insert ordered input cells into the built-in custom notebook scaffold."""
    if INPUT_MARKER not in source:
        raise GraphValidationError('Custom notebook template is missing its input insertion point.')
    names: set[str] = set()
    variables = {'mo', 'pd', 'nx', 'artifacts', 'assets', 'int', 'float', 'bool', 'str', 'list', 'dict', 'object'}
    cells: list[str] = []
    for port in inputs:
        name = str(port.get('name', ''))
        data_type = str(port.get('data_type', ''))
        if not is_valid_artifact_name(name) or name in names:
            raise GraphValidationError(f'Invalid or duplicate notebook input ID `{name}`.')
        if data_type not in CANONICAL_TYPES:
            raise GraphValidationError(f'Unsupported notebook input type `{data_type}`.')
        names.add(name)
        variable = re.sub(r'^_+', '', name) or 'input'
        if variable[0].isdigit() or keyword.iskeyword(variable):
            variable = f'input_{variable}'
        base = variable
        suffix = 2
        while variable in variables:
            variable = f'{base}_{suffix}'
            suffix += 1
        variables.add(variable)
        lines = ['@app.cell', 'def _():']
        type_expr = data_type.replace('pandas.', 'pd.').replace('networkx.', 'nx.')
        function = 'pull_file' if data_type == 'file' else 'pull'
        lines.extend([f'    {variable} = artifacts.{function}(', f'        name={name!r},'])
        if data_type != 'file':
            lines.append(f'        data_type={type_expr},')
        if port.get('default_to_none', False):
            lines.append('        allow_missing=True,' if data_type == 'file' else '        default=None,')
        lines.extend(
            [
                f'        description={str(port.get("description", ""))!r},',
                '    )',
                f'    return ({variable},)',
            ]
        )
        cells.append('\n'.join(lines))
    if any(str(port.get('data_type', '')).startswith('networkx.') for port in inputs):
        source = source.replace('    import pandas as pd', '    import pandas as pd\n    import networkx as nx', 1)
    return source.replace(INPUT_MARKER, '\n\n\n'.join(cells), 1)
