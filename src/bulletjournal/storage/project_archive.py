from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from bulletjournal.config import EXPORT_MANIFEST_VERSION
from bulletjournal.domain.errors import ProjectValidationError
from bulletjournal.storage.project_fs import ProjectPaths, load_project_json, require_project_root
from bulletjournal.utils import json_dumps, utc_now_iso


EXCLUDED_NAMES = {'.DS_Store'}
EXCLUDED_DIR_NAMES = {'__pycache__', '.runtime', '.venv', 'venv'}
REQUIRED_EXPORT_MEMBERS = {
    'export_manifest.json',
    'project/graph/meta.json',
    'project/graph/nodes.json',
    'project/graph/edges.json',
    'project/graph/layout.json',
    'project/metadata/project.json',
    'project/metadata/state.db',
    'project/pyproject.toml',
    'project/uv.lock',
}


def export_project_archive(
    project_root: Path, archive_path: Path, *, include_artifacts: bool = True
) -> dict[str, object]:
    paths = require_project_root(project_root)
    archive = archive_path.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    project_json = load_project_json(paths)
    manifest = {
        'manifest_version': EXPORT_MANIFEST_VERSION,
        'project_id': str(project_json['project_id']),
        'created_at': str(project_json['created_at']),
        'exported_at': utc_now_iso(),
        'includes_artifacts': include_artifacts,
        'format': 'bulletjournal-project-zip',
    }
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('export_manifest.json', json_dumps(manifest, pretty=True) + '\n')
        for relpath in _iter_project_files(paths, include_artifacts=include_artifacts):
            zf.write(paths.root / relpath, arcname=f'project/{relpath.as_posix()}')
    return {'archive_path': str(archive), 'project_id': manifest['project_id'], 'includes_artifacts': include_artifacts}


def import_project_archive(archive_path: Path, destination_root: Path) -> dict[str, object]:
    archive = archive_path.resolve()
    if not archive.is_file():
        raise FileNotFoundError(f'Archive not found: {archive}')
    destination = destination_root.resolve()
    if destination.exists():
        raise ProjectValidationError(f'Import destination already exists: {destination}')
    temp_root = destination.parent / f'.{destination.name}.import.tmp'
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            _validate_archive_manifest(zf, names)
            zf.extractall(temp_root)
        extracted = temp_root / 'project'
        _restore_required_directories(extracted)
        extracted_paths = require_project_root(extracted)
        project_json = load_project_json(extracted_paths)
        os_destination_parent = destination.parent
        os_destination_parent.mkdir(parents=True, exist_ok=True)
        extracted.rename(destination)
        return {'project_root': str(destination), 'project_id': str(project_json['project_id'])}
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)


def _validate_archive_manifest(zf: zipfile.ZipFile, names: set[str]) -> None:
    missing = sorted(REQUIRED_EXPORT_MEMBERS - names)
    if missing:
        raise ProjectValidationError(f'Archive is missing required members: {", ".join(missing)}')
    manifest = json.loads(zf.read('export_manifest.json').decode('utf-8'))
    if not isinstance(manifest, dict):
        raise ProjectValidationError('Archive manifest must be a JSON object.')
    if int(manifest.get('manifest_version', 0)) != EXPORT_MANIFEST_VERSION:
        raise ProjectValidationError('Unsupported export manifest version.')
    project_json = json.loads(zf.read('project/metadata/project.json').decode('utf-8'))
    if str(manifest.get('project_id') or '') != str(project_json.get('project_id') or ''):
        raise ProjectValidationError('Archive manifest project_id does not match metadata/project.json.')


def _iter_project_files(paths: ProjectPaths, *, include_artifacts: bool) -> list[Path]:
    included_roots = [
        paths.graph_dir,
        paths.notebooks_dir,
        paths.metadata_dir,
        paths.checkpoints_dir,
        paths.root / 'uploads',
    ]
    if include_artifacts:
        included_roots.append(paths.artifacts_dir)
    included_files = [paths.pyproject_path, paths.uv_lock_path]
    members: list[Path] = []
    for file_path in included_files:
        members.append(file_path.relative_to(paths.root))
    for root in included_roots:
        if not root.exists():
            continue
        for child in sorted(root.rglob('*')):
            if child.is_dir():
                if child.name in EXCLUDED_DIR_NAMES:
                    continue
                continue
            if child.name in EXCLUDED_NAMES or any(part in EXCLUDED_DIR_NAMES for part in child.parts):
                continue
            members.append(child.relative_to(paths.root))
    return members


def _restore_required_directories(root: Path) -> None:
    paths = ProjectPaths(root)
    for directory in [
        paths.graph_dir,
        paths.notebooks_dir,
        paths.metadata_dir,
        paths.artifacts_dir,
        paths.object_store_dir,
        paths.checkpoints_dir,
        paths.uploads_dir,
        paths.worker_temp_dir,
        paths.execution_logs_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)
