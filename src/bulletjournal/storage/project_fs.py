from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from bulletjournal.config import GRAPH_SCHEMA_VERSION, PROJECT_SCHEMA_VERSION, package_root
from bulletjournal.domain.errors import ProjectValidationError
from bulletjournal.storage.atomic_write import atomic_write_text
from bulletjournal.storage.state_db import StateDB
from bulletjournal.utils import ensure_directory, json_dumps, slugify, utc_now_iso

PROJECT_ID_PATTERN = re.compile(r'^[a-z0-9][a-z0-9_-]{1,62}$')


@dataclass(slots=True, frozen=True)
class ProjectPaths:
    root: Path

    @property
    def graph_dir(self) -> Path:
        return self.root / 'graph'

    @property
    def notebooks_dir(self) -> Path:
        return self.root / 'notebooks'

    @property
    def object_store_dir(self) -> Path:
        return self.root / 'objects'

    @property
    def dashboards_dir(self) -> Path:
        return self.root / 'dashboards'

    @property
    def metadata_dir(self) -> Path:
        return self.root / 'metadata'

    @property
    def state_db_path(self) -> Path:
        return self.metadata_dir / 'state.db'

    @property
    def project_json_path(self) -> Path:
        return self.metadata_dir / 'project.json'

    @property
    def pyproject_path(self) -> Path:
        return self.root / 'pyproject.toml'

    @property
    def uv_lock_path(self) -> Path:
        return self.root / 'uv.lock'

    @property
    def checkpoints_dir(self) -> Path:
        return self.root / 'checkpoints'

    @property
    def temp_dir(self) -> Path:
        return self.root / 'temp'

    @property
    def uploads_dir(self) -> Path:
        return self.temp_dir / 'uploads'

    @property
    def pulled_files_dir(self) -> Path:
        return self.temp_dir / 'pulled_files'

    @property
    def execution_logs_dir(self) -> Path:
        return self.temp_dir / 'execution_logs'

    @property
    def worker_temp_dir(self) -> Path:
        return self.temp_dir / 'worker'

    def notebook_path(self, node_id: str) -> Path:
        return self.notebooks_dir / f'{node_id}.py'

    def notebook_relpath(self, node_id: str) -> str:
        return f'notebooks/{node_id}.py'

    def dashboard_path(self, dashboard_id: str) -> Path:
        return self.dashboards_dir / f'{dashboard_id}.json'


def is_project_root(path: Path) -> bool:
    paths = ProjectPaths(path.resolve())
    required_directories = [
        paths.graph_dir,
        paths.notebooks_dir,
        paths.object_store_dir,
        paths.metadata_dir,
        paths.checkpoints_dir,
    ]
    required_files = [
        paths.graph_dir / 'meta.json',
        paths.graph_dir / 'nodes.json',
        paths.graph_dir / 'edges.json',
        paths.graph_dir / 'layout.json',
        paths.project_json_path,
        paths.state_db_path,
        paths.pyproject_path,
        paths.uv_lock_path,
    ]
    return all(directory.is_dir() for directory in required_directories) and all(
        file_path.is_file() for file_path in required_files
    )


def validate_project_id(project_id: str) -> str:
    candidate = project_id.strip()
    if not PROJECT_ID_PATTERN.fullmatch(candidate):
        raise ProjectValidationError(
            'Project id must match ^[a-z0-9][a-z0-9_-]{1,62}$.',
        )
    return candidate


def init_project_root(
    path: Path,
    title: str | None = None,
    project_id: str | None = None,
    *,
    initialize_environment: bool = True,
) -> ProjectPaths:
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = ProjectPaths(root)
    now = utc_now_iso()
    resolved_project_id = validate_project_id(project_id or slugify(root.name))

    _initialize_project_layout(paths, project_id=resolved_project_id, title=title, now=now)
    if initialize_environment:
        _initialize_project_environment(paths, project_id=resolved_project_id)
    return paths


def _initialize_project_layout(paths: ProjectPaths, *, project_id: str, title: str | None, now: str) -> None:
    ensure_directory(paths.graph_dir)
    ensure_directory(paths.notebooks_dir)
    ensure_directory(paths.object_store_dir)
    ensure_directory(paths.dashboards_dir)
    ensure_directory(paths.metadata_dir)
    ensure_directory(paths.checkpoints_dir)
    ensure_directory(paths.temp_dir)
    ensure_directory(paths.uploads_dir)
    ensure_directory(paths.pulled_files_dir)
    ensure_directory(paths.execution_logs_dir)
    ensure_directory(paths.worker_temp_dir)

    _ensure_graph_files(paths, project_id=project_id, now=now)
    _ensure_project_json(paths, project_id=project_id, title=title, now=now)
    StateDB(paths.state_db_path)


def _initialize_project_environment(paths: ProjectPaths, *, project_id: str) -> None:
    if not paths.pyproject_path.exists():
        atomic_write_text(paths.pyproject_path, _default_project_pyproject(project_id=project_id))
    if not paths.uv_lock_path.exists():
        _initialize_project_uv_lock(paths, project_id=project_id)


def load_project_json(paths: ProjectPaths) -> dict[str, object]:
    return json.loads(paths.project_json_path.read_text(encoding='utf-8'))


def _load_json_file(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise ProjectValidationError(f'{path} is not valid JSON.') from exc


def _ensure_graph_files(paths: ProjectPaths, *, project_id: str, now: str) -> None:
    meta_path = paths.graph_dir / 'meta.json'
    if meta_path.exists():
        _validate_graph_meta(meta_path, project_id=project_id)
    else:
        meta = {
            'schema_version': GRAPH_SCHEMA_VERSION,
            'project_id': project_id,
            'graph_version': 1,
            'updated_at': now,
        }
        atomic_write_text(meta_path, json_dumps(meta, pretty=True) + '\n')

    _ensure_json_list_file(paths.graph_dir / 'nodes.json')
    _ensure_json_list_file(paths.graph_dir / 'edges.json')
    _ensure_json_list_file(paths.graph_dir / 'layout.json')


def _ensure_project_json(paths: ProjectPaths, *, project_id: str, title: str | None, now: str) -> None:
    if paths.project_json_path.exists():
        project_json = load_project_json(paths)
        validate_project_schema_version(project_json, source=str(paths.project_json_path))
        _validate_project_identity(project_json, expected_project_id=project_id, source=str(paths.project_json_path))
        return

    project_json = {
        'schema_version': PROJECT_SCHEMA_VERSION,
        'project_id': project_id,
        'created_at': now,
    }
    if title is not None and title.strip():
        project_json['title'] = title.strip()
    atomic_write_text(paths.project_json_path, json_dumps(project_json, pretty=True) + '\n')


def _ensure_json_list_file(path: Path) -> None:
    if not path.exists():
        atomic_write_text(path, json_dumps([], pretty=True) + '\n')
        return
    payload = _load_json_file(path)
    if not isinstance(payload, list):
        raise ProjectValidationError(f'{path} must contain a JSON array.')


def _validate_graph_meta(path: Path, *, project_id: str) -> None:
    payload = _load_json_file(path)
    if not isinstance(payload, dict):
        raise ProjectValidationError(f'{path} must contain a JSON object.')
    raw_version = payload.get('schema_version')
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ProjectValidationError(f'{path} is missing a valid `schema_version`.') from exc
    if version != GRAPH_SCHEMA_VERSION:
        raise ProjectValidationError(
            f'Unsupported BulletJournal graph schema version {version}; expected {GRAPH_SCHEMA_VERSION}.'
        )
    _validate_project_identity(payload, expected_project_id=project_id, source=str(path))


def _validate_project_identity(payload: dict[str, object], *, expected_project_id: str, source: str) -> None:
    actual_project_id = validate_project_id(str(payload.get('project_id') or ''))
    if actual_project_id != expected_project_id:
        raise ProjectValidationError(
            f'{source} has project_id {actual_project_id!r}, expected {expected_project_id!r}.'
        )


def validate_project_schema_version(project_json: dict[str, object], *, source: str) -> None:
    raw_version = project_json.get('schema_version')
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ProjectValidationError(f'{source} is missing a valid `schema_version`.') from exc
    if version != PROJECT_SCHEMA_VERSION:
        raise ProjectValidationError(
            f'Unsupported BulletJournal project schema version {version}; expected {PROJECT_SCHEMA_VERSION}. '
            'Schema version 1 projects are no longer supported.'
        )


def require_project_root(path: Path) -> ProjectPaths:
    paths = ProjectPaths(path.resolve())
    project_json: dict[str, object] | None = None
    if paths.project_json_path.is_file():
        project_json = load_project_json(paths)
        validate_project_schema_version(project_json, source=str(paths.project_json_path))
    if not is_project_root(paths.root):
        raise ProjectValidationError(f'{paths.root} is not a valid BulletJournal project root.')
    if project_json is None:
        project_json = load_project_json(paths)
    validate_project_schema_version(project_json, source=str(paths.project_json_path))
    validate_project_id(str(project_json.get('project_id') or ''))
    ensure_directory(paths.temp_dir)
    ensure_directory(paths.dashboards_dir)
    ensure_directory(paths.execution_logs_dir)
    ensure_directory(paths.uploads_dir)
    ensure_directory(paths.pulled_files_dir)
    ensure_directory(paths.worker_temp_dir)
    return paths


def _default_project_pyproject(*, project_id: str) -> str:
    package_name = project_id.replace('_', '-').lower()
    bulletjournal_source = _local_bulletjournal_source()
    lines = [
        '[project]',
        f'name = "{package_name}"',
        'version = "0.1.0"',
        'description = "BulletJournal project environment"',
        'requires-python = ">=3.11"',
        'dependencies = [',
        '  "bulletjournal-editor",',
        ']',
    ]
    if bulletjournal_source is not None:
        lines.extend(
            [
                '',
                '[tool.uv.sources]',
                f'bulletjournal-editor = {{ path = "{bulletjournal_source.as_posix()}", editable = true }}',
            ]
        )
    return '\n'.join(lines) + '\n'


def _default_project_uv_lock(*, project_id: str) -> str:
    return textwrap.dedent(
        f"""
        version = 1
        revision = 1
        requires-python = ">=3.11"

        [[package]]
        name = "{project_id.replace('_', '-').lower()}"
        version = "0.1.0"
        source = {{ editable = "." }}
        dependencies = [
          {{ name = "bulletjournal-editor" }},
        ]
        """
    ).lstrip()


def _initialize_project_uv_lock(paths: ProjectPaths, *, project_id: str) -> None:
    uv_executable = shutil.which('uv')
    if uv_executable is not None:
        completed = subprocess.run(  # noqa: S603
            [uv_executable, 'lock', '--project', str(paths.root)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode == 0 and paths.uv_lock_path.exists():
            return
    atomic_write_text(paths.uv_lock_path, _default_project_uv_lock(project_id=project_id))


def _local_bulletjournal_source() -> Path | None:
    candidate = package_root().parent.parent
    if (candidate / 'pyproject.toml').is_file():
        return candidate
    return None
