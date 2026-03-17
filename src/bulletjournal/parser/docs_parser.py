from __future__ import annotations

import ast
from pathlib import Path

from bulletjournal.parser.marimo_loader import iter_app_cells, load_module_ast


def extract_notebook_docs(path: Path) -> str | None:
    return extract_notebook_docs_from_module(load_module_ast(path))


def extract_notebook_docs_from_module(module: ast.Module) -> str | None:
    for cell in iter_app_cells(module):
        docs = _extract_docs_from_cell(cell)
        if docs:
            return docs.strip()
        return None
    return None


def _extract_docs_from_cell(cell: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    if not cell.body:
        return None
    first_statement = cell.body[0]
    if isinstance(first_statement, ast.Expr):
        docs = _extract_markdown_call(first_statement.value)
        if docs:
            return docs
    if len(cell.body) == 1 and isinstance(first_statement, ast.Return):
        return _extract_markdown_call(first_statement.value)
    return None


def _extract_markdown_call(node: ast.AST | None) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Attribute) or not isinstance(node.func.value, ast.Name):
        return None
    if node.func.value.id != 'mo' or node.func.attr != 'md':
        return None
    if not node.args:
        return None
    first_arg = node.args[0]
    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
        return first_arg.value
    return None
