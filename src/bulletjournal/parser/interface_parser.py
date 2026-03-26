from __future__ import annotations

import ast
import json
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

from bulletjournal.domain.enums import ArtifactRole, ValidationSeverity
from bulletjournal.domain.models import NotebookInterface, Port
from bulletjournal.domain.models import ValidationIssue
from bulletjournal.domain.type_system import normalize_type_expr
from bulletjournal.parser.docs_parser import extract_notebook_docs_from_module
from bulletjournal.parser.marimo_loader import iter_app_cells
from bulletjournal.parser.source_hash import normalized_source_hash_text
from bulletjournal.parser.validation import build_issue

if TYPE_CHECKING:
    from bulletjournal.templates.provider import TemplateAsset

    NotebookSource = Path | TemplateAsset
else:
    NotebookSource = Path | object


ARTIFACT_CALLS = {'pull', 'pull_file', 'push', 'push_file'}


def parse_notebook_interface(path: NotebookSource, node_id: str) -> NotebookInterface:
    source_text, filename = _notebook_source(path)
    source_hash = normalized_source_hash_text(source_text)
    try:
        module = ast.parse(source_text, filename=filename)
    except SyntaxError as exc:
        return NotebookInterface(
            node_id=node_id,
            source_hash=source_hash,
            issues=[
                build_issue(
                    node_id=node_id,
                    severity=ValidationSeverity.ERROR,
                    code='invalid_syntax',
                    message=_syntax_error_message(exc),
                    details=_syntax_error_details(exc),
                )
            ],
        )
    except Exception as exc:  # noqa: BLE001
        return NotebookInterface(
            node_id=node_id,
            source_hash=source_hash,
            issues=[
                build_issue(
                    node_id=node_id,
                    severity=ValidationSeverity.ERROR,
                    code='notebook_parse_failed',
                    message=f'Failed to parse notebook: {exc}',
                    details={'error_type': exc.__class__.__name__},
                )
            ],
        )

    issues: list[ValidationIssue] = []
    for statement in module.body:
        unparsable_issue = _unparsable_cell_issue(statement, node_id=node_id)
        if unparsable_issue is not None:
            issues.append(unparsable_issue)
    if not _has_runtime_import(module):
        issues.append(
            build_issue(
                node_id=node_id,
                severity=ValidationSeverity.ERROR,
                code='missing_runtime_import',
                message='Notebook must import artifacts via `from bulletjournal.runtime import artifacts`.',
            )
        )

    inputs: list[Port] = []
    outputs: list[Port] = []
    assets: list[Port] = []
    seen_names: set[str] = set()
    exported_names: dict[str, int] = {}
    for cell in iter_app_cells(module):
        for statement in cell.body:
            alias_issue = _artifact_alias_issue(statement, node_id=node_id)
            if alias_issue is not None:
                issues.append(alias_issue)
            if _contains_artifact_call_nested(statement) and not _is_top_level_artifact_statement(statement):
                issues.append(
                    build_issue(
                        node_id=node_id,
                        severity=ValidationSeverity.ERROR,
                        code='nested_artifact_call',
                        message='Artifact declarations must appear at the top level of a cell body.',
                    )
                )
            parsed = _parse_statement(statement, node_id=node_id)
            if parsed is None:
                continue
            ports, new_issues = parsed
            issues.extend(new_issues)
            for port in ports:
                if port.name in seen_names:
                    issues.append(
                        build_issue(
                            node_id=node_id,
                            severity=ValidationSeverity.ERROR,
                            code='duplicate_port',
                            message=f'Duplicate artifact name `{port.name}`.',
                        )
                    )
                    continue
                seen_names.add(port.name)
                if port.direction == 'input':
                    inputs.append(port)
                elif port.role == ArtifactRole.OUTPUT:
                    outputs.append(port)
                else:
                    assets.append(port)
        _collect_duplicate_export_issues(cell, node_id=node_id, exported_names=exported_names, issues=issues)

    issues = sorted(issues, key=lambda item: (item.severity.value, item.code, item.message))
    docs = extract_notebook_docs_from_module(module)
    return NotebookInterface(
        node_id=node_id,
        source_hash=source_hash,
        inputs=inputs,
        outputs=outputs,
        assets=assets,
        docs=docs,
        issues=issues,
    )


def _notebook_source(path: NotebookSource) -> tuple[str, str]:
    if isinstance(path, Path):
        return path.read_text(encoding='utf-8'), str(path)
    filename = str(path.path) if path.path is not None else path.ref
    return path.read_text(), filename


def _syntax_error_message(exc: SyntaxError) -> str:
    message = exc.msg.strip() if isinstance(exc.msg, str) and exc.msg.strip() else 'invalid syntax'
    if exc.lineno is None:
        return f'Syntax error: {message}.'
    return f'Syntax error on line {exc.lineno}: {message}.'


def _syntax_error_details(exc: SyntaxError) -> dict[str, object]:
    details: dict[str, object] = {}
    if exc.lineno is not None:
        details['line'] = exc.lineno
    if exc.offset is not None:
        details['column'] = exc.offset
    if isinstance(exc.text, str) and exc.text.strip():
        details['source'] = exc.text.rstrip('\n')
    return details


def _unparsable_cell_issue(statement: ast.stmt, *, node_id: str) -> ValidationIssue | None:
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return None
    call = statement.value
    if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name):
        return None
    if call.func.value.id != 'app' or call.func.attr != '_unparsable_cell' or not call.args:
        return None

    raw_source = call.args[0]
    if not isinstance(raw_source, ast.Constant) or not isinstance(raw_source.value, str):
        return build_issue(
            node_id=node_id,
            severity=ValidationSeverity.ERROR,
            code='invalid_syntax',
            message='Marimo saved a cell that could not be parsed.',
            details={'cell_line': statement.lineno},
        )

    cell_source = dedent(raw_source.value).strip('\n')
    details: dict[str, object] = {'cell_line': statement.lineno, 'source': cell_source}
    try:
        ast.parse(cell_source)
    except SyntaxError as exc:
        if exc.lineno is not None:
            details['line_in_cell'] = exc.lineno
        if exc.offset is not None:
            details['column'] = exc.offset
        if isinstance(exc.text, str) and exc.text.strip():
            details['source_line'] = exc.text.rstrip('\n')
        message = exc.msg.strip() if isinstance(exc.msg, str) and exc.msg.strip() else 'invalid syntax'
        return build_issue(
            node_id=node_id,
            severity=ValidationSeverity.ERROR,
            code='invalid_syntax',
            message=f'Syntax error in a Marimo cell: {message}.',
            details=details,
        )

    return build_issue(
        node_id=node_id,
        severity=ValidationSeverity.ERROR,
        code='invalid_syntax',
        message='Marimo saved a cell that could not be parsed.',
        details=details,
    )


def _collect_duplicate_export_issues(
    cell: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    node_id: str,
    exported_names: dict[str, int],
    issues: list[ValidationIssue],
) -> None:
    for name in _cell_exported_names(cell):
        first_line = exported_names.get(name)
        if first_line is None:
            exported_names[name] = cell.lineno
            continue
        issues.append(
            build_issue(
                node_id=node_id,
                severity=ValidationSeverity.ERROR,
                code='duplicate_cell_global',
                message=f'Global name `{name}` is defined by multiple Marimo cells.',
                details={'name': name, 'first_cell_line': first_line, 'duplicate_cell_line': cell.lineno},
            )
        )


def _cell_exported_names(cell: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    exported: list[str] = []
    for statement in cell.body:
        if not isinstance(statement, ast.Return):
            continue
        exported.extend(_return_value_names(statement.value))
    return exported


def _return_value_names(node: ast.AST | None) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Tuple):
        names: list[str] = []
        for item in node.elts:
            if isinstance(item, ast.Name):
                names.append(item.id)
        return names
    return []


def _has_runtime_import(module: ast.Module) -> bool:
    for node in module.body:
        if _is_runtime_import(node):
            return True
        setup_block = _app_setup_block(node)
        if setup_block is not None:
            for statement in setup_block.body:
                if _is_runtime_import(statement):
                    return True
    return False


def _is_runtime_import(node: ast.stmt) -> bool:
    if isinstance(node, ast.ImportFrom) and node.module == 'bulletjournal.runtime':
        for alias in node.names:
            if alias.name == 'artifacts' and alias.asname is None:
                return True
    return False


def _app_setup_block(node: ast.stmt) -> ast.With | None:
    if not isinstance(node, ast.With) or len(node.items) != 1:
        return None
    context_expr = node.items[0].context_expr
    if isinstance(context_expr, ast.Attribute):
        if isinstance(context_expr.value, ast.Name) and context_expr.value.id == 'app' and context_expr.attr == 'setup':
            return node
        return None
    if isinstance(context_expr, ast.Call) and isinstance(context_expr.func, ast.Attribute):
        if (
            isinstance(context_expr.func.value, ast.Name)
            and context_expr.func.value.id == 'app'
            and context_expr.func.attr == 'setup'
        ):
            return node
    return None


def _contains_artifact_call_nested(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _is_artifact_call(child):
            return True
        if isinstance(child, ast.With):
            for item in child.items:
                if isinstance(item.context_expr, ast.Call) and _is_artifact_call(item.context_expr):
                    return True
    return False


def _is_top_level_artifact_statement(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
        return _is_artifact_call(statement.value)
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
        return _is_artifact_call(statement.value)
    if isinstance(statement, ast.With) and len(statement.items) == 1:
        return isinstance(statement.items[0].context_expr, ast.Call) and _is_artifact_call(
            statement.items[0].context_expr
        )
    return False


def _artifact_alias_issue(statement: ast.stmt, *, node_id: str) -> ValidationIssue | None:
    if not isinstance(statement, ast.Assign):
        return None
    if _is_artifact_attribute(statement.value):
        return build_issue(
            node_id=node_id,
            severity=ValidationSeverity.ERROR,
            code='artifact_aliasing',
            message='Aliasing artifact runtime helpers is not supported; call `artifacts.pull/push/...` directly.',
        )
    return None


def _is_artifact_call(call: ast.Call) -> bool:
    return (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == 'artifacts'
        and call.func.attr in ARTIFACT_CALLS
    )


def _is_artifact_attribute(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == 'artifacts'
        and node.attr in ARTIFACT_CALLS
    )


def _parse_statement(statement: ast.stmt, *, node_id: str) -> tuple[list[Port], list[ValidationIssue]] | None:
    issues: list[ValidationIssue] = []
    if (
        isinstance(statement, ast.Assign)
        and isinstance(statement.value, ast.Call)
        and _is_artifact_call(statement.value)
    ):
        call = statement.value
        call_name = _artifact_call_name(call)
        if call_name == 'pull':
            port, port_issues = _parse_pull(call, node_id=node_id)
            issues.extend(port_issues)
            return ([port] if port else []), issues
        if call_name == 'pull_file':
            port, port_issues = _parse_pull_file(call, node_id=node_id)
            issues.extend(port_issues)
            return ([port] if port else []), issues
        issues.append(
            build_issue(
                node_id=node_id,
                severity=ValidationSeverity.ERROR,
                code='invalid_assignment_call',
                message=f'`artifacts.{call_name}` cannot be assigned this way.',
            )
        )
        return [], issues
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call) and _is_artifact_call(statement.value):
        call = statement.value
        call_name = _artifact_call_name(call)
        if call_name == 'push':
            port, port_issues = _parse_push(call, node_id=node_id)
            issues.extend(port_issues)
            return ([port] if port else []), issues
        issues.append(
            build_issue(
                node_id=node_id,
                severity=ValidationSeverity.ERROR,
                code='invalid_expression_call',
                message=f'`artifacts.{call_name}` must follow the supported top-level syntax.',
            )
        )
        return [], issues
    if isinstance(statement, ast.With) and len(statement.items) == 1:
        item = statement.items[0]
        if isinstance(item.context_expr, ast.Call) and _is_artifact_call(item.context_expr):
            call = item.context_expr
            if _artifact_call_name(call) == 'push_file':
                port, port_issues = _parse_push_file(call, node_id=node_id)
                issues.extend(port_issues)
                return ([port] if port else []), issues
    return None


def _parse_pull(call: ast.Call, *, node_id: str) -> tuple[Port | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    kwargs = {item.arg: item.value for item in call.keywords if item.arg is not None}
    name = _literal_string(kwargs.get('name'))
    if name is None:
        issues.append(_literal_issue(node_id, 'name', 'Input name must be a literal string.'))
        return None, issues
    data_type, type_warning = normalize_type_expr(kwargs.get('data_type'))
    if type_warning:
        issues.append(_type_warning(node_id, name))
    default_value, has_default, default_issue = _parse_default(kwargs.get('default'), node_id=node_id)
    if default_issue:
        issues.append(default_issue)
    description = _literal_string(kwargs.get('description')) if 'description' in kwargs else None
    if 'description' in kwargs and description is None:
        issues.append(_literal_issue(node_id, 'description', 'Description must be a literal string.'))
    return (
        Port(
            name=name,
            data_type=data_type,
            role=None,
            description=description,
            default=default_value,
            has_default=has_default,
            kind='input',
            direction='input',
        ),
        issues,
    )


def _parse_pull_file(call: ast.Call, *, node_id: str) -> tuple[Port | None, list[ValidationIssue]]:
    kwargs = {item.arg: item.value for item in call.keywords if item.arg is not None}
    name = _literal_string(kwargs.get('name'))
    if name is None:
        return None, [_literal_issue(node_id, 'name', 'File input name must be a literal string.')]
    description = _literal_string(kwargs.get('description')) if 'description' in kwargs else None
    issues: list[ValidationIssue] = []
    if 'description' in kwargs and description is None:
        issues.append(_literal_issue(node_id, 'description', 'Description must be a literal string.'))
    allow_missing_value = _literal_bool(kwargs.get('allow_missing')) if 'allow_missing' in kwargs else False
    if 'allow_missing' in kwargs and allow_missing_value is None:
        issues.append(_literal_issue(node_id, 'allow_missing', 'allow_missing must be a literal boolean.'))
        allow_missing_value = False
    allow_missing = bool(allow_missing_value)
    return (
        Port(
            name=name,
            data_type='file',
            role=None,
            description=description,
            default=None,
            has_default=allow_missing,
            kind='input',
            direction='input',
        ),
        issues,
    )


def _parse_push(call: ast.Call, *, node_id: str) -> tuple[Port | None, list[ValidationIssue]]:
    kwargs = {item.arg: item.value for item in call.keywords if item.arg is not None}
    name = _literal_string(kwargs.get('name'))
    if name is None:
        return None, [_literal_issue(node_id, 'name', 'Output name must be a literal string.')]
    data_type, type_warning = normalize_type_expr(kwargs.get('data_type'))
    issues: list[ValidationIssue] = []
    if type_warning:
        issues.append(_type_warning(node_id, name))
    description = _literal_string(kwargs.get('description')) if 'description' in kwargs else None
    if 'description' in kwargs and description is None:
        issues.append(_literal_issue(node_id, 'description', 'Description must be a literal string.'))
    is_output = _literal_bool(kwargs.get('is_output')) if 'is_output' in kwargs else False
    if 'is_output' in kwargs and is_output is None:
        issues.append(_literal_issue(node_id, 'is_output', 'is_output must be a literal boolean.'))
        is_output = False
    role = ArtifactRole.OUTPUT if is_output else ArtifactRole.ASSET
    return Port(name=name, data_type=data_type, role=role, description=description, kind='value'), issues


def _parse_push_file(call: ast.Call, *, node_id: str) -> tuple[Port | None, list[ValidationIssue]]:
    kwargs = {item.arg: item.value for item in call.keywords if item.arg is not None}
    name = _literal_string(kwargs.get('name'))
    if name is None:
        return None, [_literal_issue(node_id, 'name', 'File output name must be a literal string.')]
    description = _literal_string(kwargs.get('description')) if 'description' in kwargs else None
    issues: list[ValidationIssue] = []
    if 'description' in kwargs and description is None:
        issues.append(_literal_issue(node_id, 'description', 'Description must be a literal string.'))
    is_output = _literal_bool(kwargs.get('is_output')) if 'is_output' in kwargs else False
    if 'is_output' in kwargs and is_output is None:
        issues.append(_literal_issue(node_id, 'is_output', 'is_output must be a literal boolean.'))
        is_output = False
    role = ArtifactRole.OUTPUT if is_output else ArtifactRole.ASSET
    return Port(name=name, data_type='file', role=role, description=description, kind='file'), issues


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_bool(node: ast.AST | None) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _parse_default(node: ast.AST | None, *, node_id: str) -> tuple[object | None, bool, ValidationIssue | None]:
    if node is None:
        return None, False, None
    try:
        value = ast.literal_eval(node)
        json.dumps(value)
        return value, True, None
    except (ValueError, SyntaxError):
        return (
            None,
            False,
            build_issue(
                node_id=node_id,
                severity=ValidationSeverity.ERROR,
                code='invalid_default',
                message='Default must be a literal JSON-serializable value.',
            ),
        )
    except TypeError:
        return (
            None,
            False,
            build_issue(
                node_id=node_id,
                severity=ValidationSeverity.ERROR,
                code='invalid_default',
                message='Default must be JSON-serializable.',
            ),
        )


def _literal_issue(node_id: str, field_name: str, message: str) -> ValidationIssue:
    return build_issue(
        node_id=node_id,
        severity=ValidationSeverity.ERROR,
        code=f'invalid_{field_name}',
        message=message,
    )


def _type_warning(node_id: str, artifact_name: str) -> ValidationIssue:
    return build_issue(
        node_id=node_id,
        severity=ValidationSeverity.WARNING,
        code='unknown_type',
        message=f'Artifact `{artifact_name}` type could not be parsed and was normalized to `object`.',
    )


def _artifact_call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    raise ValueError('Unsupported artifact call.')
