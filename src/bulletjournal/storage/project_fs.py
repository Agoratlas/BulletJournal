from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
import re
import textwrap

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
    def uploads_temp_dir(self) -> Path:
        return self.temp_dir / 'uploads'

    @property
    def execution_logs_dir(self) -> Path:
        return self.temp_dir / 'execution_logs'

    def notebook_path(self, node_id: str) -> Path:
        return self.notebooks_dir / f'{node_id}.py'

    def notebook_relpath(self, node_id: str) -> str:
        return f'notebooks/{node_id}.py'


def is_project_root(path: Path) -> bool:
    paths = ProjectPaths(path.resolve())
    required_directories = [
        paths.graph_dir,
        paths.notebooks_dir,
        paths.artifacts_dir,
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


def init_project_root(path: Path, title: str | None = None, project_id: str | None = None) -> ProjectPaths:
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = ProjectPaths(root)
    ensure_directory(paths.graph_dir)
    ensure_directory(paths.notebooks_dir)
    ensure_directory(paths.object_store_dir)
    ensure_directory(paths.metadata_dir)
    ensure_directory(paths.checkpoints_dir)
    ensure_directory(paths.temp_dir)
    ensure_directory(paths.uploads_temp_dir)
    ensure_directory(paths.execution_logs_dir)

    now = utc_now_iso()
    resolved_project_id = validate_project_id(project_id or slugify(root.name))

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
        'created_at': now,
    }
    if title is not None and title.strip():
        project_json['title'] = title.strip()
    atomic_write_text(paths.project_json_path, json_dumps(project_json, pretty=True) + '\n')

    atomic_write_text(paths.pyproject_path, _default_project_pyproject(project_id=resolved_project_id))
    _initialize_project_uv_lock(paths, project_id=resolved_project_id)
    StateDB(paths.state_db_path)
    return paths


def load_project_json(paths: ProjectPaths) -> dict[str, object]:
    return json.loads(paths.project_json_path.read_text(encoding='utf-8'))


def require_project_root(path: Path) -> ProjectPaths:
    paths = ProjectPaths(path.resolve())
    if not is_project_root(paths.root):
        raise ProjectValidationError(f'{paths.root} is not a valid BulletJournal project root.')
    ensure_directory(paths.temp_dir)
    ensure_directory(paths.execution_logs_dir)
    ensure_directory(paths.uploads_temp_dir)
    project_json = load_project_json(paths)
    validate_project_id(str(project_json.get('project_id') or ''))
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
        '  "bulletjournal",',
        ']',
    ]
    if bulletjournal_source is not None:
        lines.extend(
            [
                '',
                '[tool.uv.sources]',
                f'bulletjournal = {{ path = "{bulletjournal_source.as_posix()}", editable = true }}',
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
          {{ name = "bulletjournal" }},
        ]
        """
    ).lstrip()


def _initialize_project_uv_lock(paths: ProjectPaths, *, project_id: str) -> None:
    uv_executable = shutil.which('uv')
    if uv_executable is not None:
        completed = subprocess.run(
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
