from __future__ import annotations

import importlib.metadata
import json
from dataclasses import dataclass
from pathlib import Path

from bulletjournal.config import ENVIRONMENT_SCHEMA_VERSION, GRAPH_SCHEMA_VERSION, PROJECT_SCHEMA_VERSION
from bulletjournal.storage.atomic_write import atomic_write_text
from bulletjournal.utils import ensure_directory, json_dumps, python_version_string, slugify, utc_now_iso


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
    def artifacts_dir(self) -> Path:
        return self.root / 'artifacts'

    @property
    def object_store_dir(self) -> Path:
        return self.artifacts_dir / 'objects'

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
    def environment_json_path(self) -> Path:
        return self.metadata_dir / 'environment.json'

    @property
    def checkpoints_dir(self) -> Path:
        return self.root / 'checkpoints'

    @property
    def uploads_temp_dir(self) -> Path:
        return self.root / 'uploads' / 'temp'

    def notebook_path(self, node_id: str) -> Path:
        return self.notebooks_dir / f'{node_id}.py'

    def notebook_relpath(self, node_id: str) -> str:
        return f'notebooks/{node_id}.py'


def is_project_root(path: Path) -> bool:
    paths = ProjectPaths(path.resolve())
    return paths.graph_dir.exists() and paths.metadata_dir.exists() and paths.notebooks_dir.exists()


def _bulletjournal_version() -> str:
    try:
        return importlib.metadata.version('bulletjournal')
    except importlib.metadata.PackageNotFoundError:
        return '0.1.0'


def init_project_root(path: Path, title: str | None = None, project_id: str | None = None) -> ProjectPaths:
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = ProjectPaths(root)
    ensure_directory(paths.graph_dir)
    ensure_directory(paths.notebooks_dir)
    ensure_directory(paths.object_store_dir)
    ensure_directory(paths.metadata_dir)
    ensure_directory(paths.checkpoints_dir)
    ensure_directory(paths.uploads_temp_dir)

    now = utc_now_iso()
    resolved_title = title or root.name.replace('_', ' ').replace('-', ' ').title()
    resolved_project_id = project_id or slugify(root.name)

    meta = {
        'schema_version': GRAPH_SCHEMA_VERSION,
        'project_id': resolved_project_id,
        'graph_version': 1,
        'updated_at': now,
    }
    nodes: list[dict[str, object]] = []
    edges: list[dict[str, object]] = []
    layout: list[dict[str, object]] = []
    atomic_write_text(paths.graph_dir / 'meta.json', json_dumps(meta, pretty=True) + '\n')
    atomic_write_text(paths.graph_dir / 'nodes.json', json_dumps(nodes, pretty=True) + '\n')
    atomic_write_text(paths.graph_dir / 'edges.json', json_dumps(edges, pretty=True) + '\n')
    atomic_write_text(paths.graph_dir / 'layout.json', json_dumps(layout, pretty=True) + '\n')

    project_json = {
        'schema_version': PROJECT_SCHEMA_VERSION,
        'project_id': resolved_project_id,
        'title': resolved_title,
        'created_at': now,
        'artifact_cache_limit_bytes': 20_000_000_000,
        'tracked_env_vars': [],
        'default_open_browser': True,
    }
    atomic_write_text(paths.project_json_path, json_dumps(project_json, pretty=True) + '\n')

    environment_json = {
        'schema_version': ENVIRONMENT_SCHEMA_VERSION,
        'python_version': python_version_string(),
        'bulletjournal_version': _bulletjournal_version(),
        'marimo_version': _safe_dependency_version('marimo'),
        'package_snapshot_format': 'pip_freeze_text',
        'package_snapshot_path': 'metadata/environment_packages.txt',
        'tracked_env_vars': [],
    }
    atomic_write_text(paths.environment_json_path, json_dumps(environment_json, pretty=True) + '\n')
    atomic_write_text(paths.metadata_dir / 'environment_packages.txt', '')
    return paths


def _safe_dependency_version(package_name: str) -> str | None:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def load_project_json(paths: ProjectPaths) -> dict[str, object]:
    return json.loads(paths.project_json_path.read_text(encoding='utf-8'))
