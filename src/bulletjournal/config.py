from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8765
GRAPH_SCHEMA_VERSION = 1
PROJECT_SCHEMA_VERSION = 2
ENVIRONMENT_SCHEMA_VERSION = 1
EXPORT_MANIFEST_VERSION = 1
WATCH_INTERVAL_SECONDS = 1.0
CHECKPOINT_DEBOUNCE_MINUTES = 5
EDIT_STABILIZATION_SECONDS = 2.0
MAX_PREVIEW_ROWS = 5
MAX_PREVIEW_COLS = 50
MAX_SIMPLE_PREVIEW_CHARS = 400
IMAGE_PREVIEW_MAX_BYTES = 10_000_000
DB_TIMEOUT_SECONDS = 30.0
SSE_POLL_INTERVAL_SECONDS = 1.0
SSE_EVENT_RETENTION = 1000
WEB_DIST_DIRNAME = '_web'
_TRUE_ENV_VALUES = {'1', 'true', 'yes', 'on'}
_FALSE_ENV_VALUES = {'0', 'false', 'no', 'off'}


@dataclass(slots=True, frozen=True)
class ServerConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    base_path: str = ''
    open_browser: bool = False
    reload: bool = False
    dev_frontend_url: str | None = None
    controller_token: str | None = None


def package_root() -> Path:
    return Path(__file__).resolve().parent


def bundled_web_root() -> Path:
    return package_root() / WEB_DIST_DIRNAME


def normalize_base_path(value: str | None) -> str:
    if value is None:
        return ''
    stripped = value.strip()
    if not stripped or stripped == '/':
        return ''
    return '/' + stripped.strip('/')


def controller_token_from_env() -> str | None:
    value = os.environ.get('BULLETJOURNAL_CONTROLLER_TOKEN')
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def mcp_enabled_from_env() -> bool:
    """Return the explicitly enabled MCP feature flag.

    An invalid value is a configuration error, rather than an accidental public
    endpoint exposure.
    """
    value = os.environ.get('BULLETJOURNAL_ENABLE_MCP')
    if value is None or not value.strip():
        return False
    normalized = value.strip().lower()
    if normalized in _TRUE_ENV_VALUES:
        return True
    if normalized in _FALSE_ENV_VALUES:
        return False
    accepted = ', '.join(sorted(_TRUE_ENV_VALUES | _FALSE_ENV_VALUES))
    raise ValueError(f'Invalid BULLETJOURNAL_ENABLE_MCP value. Expected one of: {accepted}.')


def mcp_bearer_token_from_env() -> str | None:
    value = os.environ.get('BULLETJOURNAL_MCP_TOKEN')
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
