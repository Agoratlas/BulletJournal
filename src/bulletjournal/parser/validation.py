from __future__ import annotations

import hashlib
from typing import Any

from bulletjournal.domain.enums import ValidationSeverity
from bulletjournal.domain.models import ValidationIssue
from bulletjournal.utils import json_dumps


def build_issue_id(
    *,
    node_id: str,
    severity: ValidationSeverity,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> str:
    normalized_details = details or {}
    fingerprint = '|'.join(
        [
            node_id,
            severity.value,
            code,
            message,
            json_dumps(normalized_details),
        ]
    )
    return hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()


def build_issue(
    *,
    node_id: str,
    severity: ValidationSeverity,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> ValidationIssue:
    normalized_details = details or {}
    return ValidationIssue(
        issue_id=build_issue_id(
            node_id=node_id,
            severity=severity,
            code=code,
            message=message,
            details=normalized_details,
        ),
        node_id=node_id,
        severity=severity,
        code=code,
        message=message,
        details=normalized_details,
    )
