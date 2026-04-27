from __future__ import annotations

from enum import StrEnum


class NodeKind(StrEnum):
    NOTEBOOK = 'notebook'
    CONSTANT = 'constant'
    FILE_INPUT = 'file_input'
    ORGANIZER = 'organizer'
    AREA = 'area'


class ArtifactRole(StrEnum):
    OUTPUT = 'output'
    ASSET = 'asset'


class ArtifactState(StrEnum):
    READY = 'ready'
    STALE = 'stale'
    PENDING = 'pending'


class RunMode(StrEnum):
    RUN_STALE = 'run_stale'
    RUN_ALL = 'run_all'
    EDIT_RUN = 'edit_run'


class LineageMode(StrEnum):
    MANAGED = 'managed'
    INTERACTIVE_HEURISTIC = 'interactive_heuristic'


class ValidationSeverity(StrEnum):
    ERROR = 'error'
    WARNING = 'warning'


class StorageKind(StrEnum):
    JSON = 'json'
    PARQUET = 'parquet'
    FILE = 'file'
    PICKLE = 'pickle'


class RunStatus(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    ABORTED_ON_RESTART = 'aborted_on_restart'
