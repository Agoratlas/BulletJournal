from __future__ import annotations

from typing import Any

from bulletjournal.domain.errors import GraphValidationError, InvalidRequestError, NotFoundError, RunConflictError
from bulletjournal.services.dashboard_service import DashboardVersionConflictError


def tool_error(
    code: str, message: str, *, retryable: bool = False, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {'ok': False, 'error': {'code': code, 'message': message, 'retryable': retryable, 'details': details or {}}}


def map_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, NotFoundError | FileNotFoundError):
        return tool_error('not_found', str(exc))
    if isinstance(exc, RunConflictError):
        return tool_error('run_conflict', str(exc), retryable=True)
    if isinstance(exc, DashboardVersionConflictError):
        return tool_error(
            'dashboard_version_conflict',
            'The dashboard changed after it was read.',
            retryable=True,
            details={'dashboard': exc.latest_dashboard},
        )
    if isinstance(exc, GraphValidationError):
        message = str(exc)
        if message == 'Graph version conflict.':
            return tool_error('graph_version_conflict', 'The graph changed after it was read.', retryable=True)
        if 'frozen block' in message.lower():
            return tool_error('frozen_block', message)
        return tool_error('validation_failed', message)
    if isinstance(exc, InvalidRequestError | ValueError | TypeError):
        message = str(exc)
        if 'frozen block' in message.lower():
            return tool_error('frozen_block', message)
        return tool_error('invalid_argument', message)
    return tool_error('internal_error', 'The operation could not be completed.')
