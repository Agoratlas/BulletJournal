from __future__ import annotations

import ast
from pathlib import Path


def load_module_ast(path: Path) -> ast.Module:
    source = path.read_text(encoding='utf-8')
    return ast.parse(source, filename=str(path))


def iter_app_cells(module: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _has_app_cell_decorator(node)
    ]


def _has_app_cell_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Attribute) and isinstance(decorator.value, ast.Name):
            if decorator.value.id == 'app' and decorator.attr == 'cell':
                return True
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
        ):
            if decorator.func.value.id == 'app' and decorator.func.attr == 'cell':
                return True
    return False
