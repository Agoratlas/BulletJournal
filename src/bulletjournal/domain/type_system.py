from __future__ import annotations

import ast


CANONICAL_TYPES = {
    'int',
    'float',
    'bool',
    'str',
    'list',
    'dict',
    'pandas.DataFrame',
    'pandas.Series',
    'networkx.Graph',
    'networkx.DiGraph',
    'file',
    'object',
}


def normalize_type_expr(node: ast.AST | None) -> tuple[str, bool]:
    if node is None:
        return 'object', True
    if isinstance(node, ast.Name) and node.id in {'int', 'float', 'bool', 'str', 'list', 'dict', 'object'}:
        return node.id, False
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        root = node.value.id
        attr = node.attr
        if root in {'pd', 'pandas'} and attr in {'DataFrame', 'Series'}:
            return f'pandas.{attr}', False
        if root in {'nx', 'networkx'} and attr in {'Graph', 'DiGraph'}:
            return f'networkx.{attr}', False
    return 'object', True


def types_compatible(source_type: str, target_type: str) -> bool:
    return source_type == target_type
