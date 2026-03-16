from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8765
GRAPH_SCHEMA_VERSION = 1
PROJECT_SCHEMA_VERSION = 1
ENVIRONMENT_SCHEMA_VERSION = 1
WATCH_INTERVAL_SECONDS = 1.0
CHECKPOINT_DEBOUNCE_MINUTES = 5
EDIT_STABILIZATION_SECONDS = 2.0
MAX_PREVIEW_ROWS = 5
MAX_PREVIEW_COLS = 8
MAX_SIMPLE_PREVIEW_CHARS = 400
IMAGE_PREVIEW_MAX_BYTES = 1_000_000
DB_TIMEOUT_SECONDS = 30.0
SSE_POLL_INTERVAL_SECONDS = 1.0
SSE_EVENT_RETENTION = 1000
WEB_DIST_DIRNAME = '_web'


@dataclass(slots=True, frozen=True)
class ServerConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    open_browser: bool = False
    reload: bool = False
    dev_frontend_url: str | None = None


def package_root() -> Path:
    return Path(__file__).resolve().parent


def bundled_web_root() -> Path:
    return package_root() / WEB_DIST_DIRNAME
