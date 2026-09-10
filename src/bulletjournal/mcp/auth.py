from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


def validate_local_request(request: Request, *, token: str | None, allowed_origins: set[str]) -> None:
    origin = request.headers.get('origin')
    if origin and origin not in allowed_origins:
        raise HTTPException(status_code=403, detail='Unexpected MCP Origin.')
    if token is not None:
        authorization = request.headers.get('authorization')
        if authorization != f'Bearer {token}':
            raise HTTPException(status_code=401, detail='MCP bearer token is required.')


def is_controller_request(request: Request, *, controller_token: str | None) -> bool:
    token = request.headers.get('x-bulletjournal-controller-token')
    assertion = request.headers.get('x-bulletjournal-controller-assertion')
    if token is None and assertion is None:
        return False
    if not controller_token or not token or not hmac.compare_digest(token, controller_token):
        raise HTTPException(status_code=401, detail='Invalid Controller MCP credential.')
    if not assertion or not assertion.startswith('user:') or not assertion[5:].strip():
        raise HTTPException(status_code=401, detail='Invalid Controller MCP assertion.')
    return True


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower().strip('[]')
    return normalized in {'127.0.0.1', '::1', 'localhost'}


def request_host(request: Request) -> str:
    return (urlsplit(f'//{request.headers.get("host", "")}').hostname or '').lower()
