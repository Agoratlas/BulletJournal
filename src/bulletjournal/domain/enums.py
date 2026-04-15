from __future__ import annotations

from enum import Enum


class NodeKind(str, Enum):
    NOTEBOOK = 'notebook'
    FILE_INPUT = 'file_input'
    ORGANIZER = 'organizer'
    AREA = 'area'


class ArtifactRole(str, Enum):
    OUTPUT = 'output'
    ASSET = 'asset'


class ArtifactState(str, Enum):
    READY = 'ready'
    STALE = 'stale'
    PENDING = 'pending'


class RunMode(str, Enum):
    RUN_STALE = 'run_stale'
    RUN_ALL = 'run_all'
    EDIT_RUN = 'edit_run'


class LineageMode(str, Enum):
    MANAGED = 'managed'
    INTERACTIVE_HEURISTIC = 'interactive_heuristic'


class ValidationSeverity(str, Enum):
    ERROR = 'error'
    WARNING = 'warning'


class StorageKind(str, Enum):
    JSON = 'json'
    PARQUET = 'parquet'
    FILE = 'file'
    PICKLE = 'pickle'


class RunStatus(str, Enum):
    QUEUED = 'queued'
    RUNNING = 'running'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    ABORTED_ON_RESTART = 'aborted_on_restart'
