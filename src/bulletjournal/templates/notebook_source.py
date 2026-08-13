from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(slots=True)
class MarimoAppDefinition:
    lineno: int
    end_lineno: int
    prefix: str
    suffix: str
    newline: str
    positional_args: list[str]
    keyword_args: list[tuple[str, str]]


def validate_rewritable_marimo_app_definition(source_text: str) -> str | None:
    _, error = inspect_marimo_app_definition(source_text)
    return error


def rewrite_marimo_app_title(source_text: str, *, node_id: str) -> str:
    definition, error = inspect_marimo_app_definition(source_text)
    if definition is None:
        raise ValueError(error or 'Notebook template must define `app = marimo.App(...)` at the top level.')
    return rewrite_marimo_app_title_from_definition(source_text, definition=definition, node_id=node_id)


def rewrite_marimo_app_title_from_definition(source_text: str, *, definition: MarimoAppDefinition, node_id: str) -> str:
    """Rewrite a source using an app definition already parsed during template discovery."""

    arguments = [*definition.positional_args]
    arguments.extend(f'{name}={value}' for name, value in definition.keyword_args if name != 'app_title')
    arguments.append(f'app_title={node_id!r}')
    replacement = f'{definition.prefix}app = marimo.App({", ".join(arguments)}){definition.suffix}{definition.newline}'

    lines = source_text.splitlines(keepends=True)
    lines[definition.lineno - 1 : definition.end_lineno] = [replacement]
    return ''.join(lines)


def inspect_marimo_app_definition(source_text: str) -> tuple[MarimoAppDefinition | None, str | None]:
    try:
        module = ast.parse(source_text)
    except SyntaxError:
        return None, None

    matches: list[tuple[ast.Assign, ast.Call]] = []
    for statement in module.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name) or target.id != 'app':
            continue
        call = statement.value
        if not isinstance(call, ast.Call):
            continue
        if not isinstance(call.func, ast.Attribute) or call.func.attr != 'App':
            continue
        if not isinstance(call.func.value, ast.Name) or call.func.value.id != 'marimo':
            continue
        matches.append((statement, call))

    if not matches:
        return None, 'Notebook template must define `app = marimo.App(...)` at the top level.'
    if len(matches) != 1:
        return None, 'Notebook template must define exactly one top-level `app = marimo.App(...)` statement.'

    statement, call = matches[0]
    lineno = getattr(statement, 'lineno', None)
    end_lineno = getattr(statement, 'end_lineno', None)
    end_col_offset = getattr(statement, 'end_col_offset', None)
    col_offset = getattr(statement, 'col_offset', None)
    if lineno is None or end_lineno is None or end_col_offset is None or col_offset is None:
        return None, 'Notebook template app definition could not be located.'
    lines = source_text.splitlines(keepends=True)
    if lineno < 1 or end_lineno > len(lines):
        return None, 'Notebook template app definition could not be located.'
    start_line = lines[lineno - 1]
    end_line = lines[end_lineno - 1]
    start_line_without_newline = start_line.rstrip('\r\n')
    end_line_without_newline = end_line.rstrip('\r\n')
    newline = end_line[len(end_line_without_newline) :]

    positional_args: list[str] = []
    for arg in call.args:
        segment = ast.get_source_segment(source_text, arg)
        if segment is None:
            return None, 'Notebook template app definition must use inline arguments that can be rewritten.'
        positional_args.append(segment.strip())

    keyword_args: list[tuple[str, str]] = []
    for keyword in call.keywords:
        if keyword.arg is None:
            return None, 'Notebook template app definition must not use `**kwargs`.'
        segment = ast.get_source_segment(source_text, keyword.value)
        if segment is None:
            return None, 'Notebook template app definition must use inline keyword values that can be rewritten.'
        keyword_args.append((keyword.arg, segment.strip()))

    return (
        MarimoAppDefinition(
            lineno=lineno,
            end_lineno=end_lineno,
            prefix=start_line_without_newline[:col_offset],
            suffix=end_line_without_newline[end_col_offset:],
            newline=newline,
            positional_args=positional_args,
            keyword_args=keyword_args,
        ),
        None,
    )
