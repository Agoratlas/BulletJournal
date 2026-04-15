from __future__ import annotations

from pathlib import Path

from bulletjournal.templates.builtin_provider import BUILTIN_PROVIDER
from bulletjournal.templates.registry import (
    builtin_pipeline_templates,
    builtin_templates,
    default_notebook_assets,
    example_pipeline_templates,
    example_templates,
)
from bulletjournal.templates.validator import BUILTIN_NOTEBOOK_TEMPLATE_ROOT, validate_template


def validate_templates(path: str | None = None) -> list[dict[str, object]]:
    if path is None:
        template_paths = [
            *builtin_templates(),
            *example_templates(),
            *builtin_pipeline_templates(),
            *example_pipeline_templates(),
        ]
        notebook_paths_by_ref = {}
        for asset in default_notebook_assets():
            if asset.path is None:
                continue
            notebook_paths_by_ref[asset.ref] = asset.path
            notebook_paths_by_ref[asset.file_name] = asset.path
            notebook_paths_by_ref[asset.name] = asset.path
            for alias in asset.aliases:
                notebook_paths_by_ref[alias] = asset.path
            if asset.provider == BUILTIN_PROVIDER and asset.path.is_relative_to(BUILTIN_NOTEBOOK_TEMPLATE_ROOT):
                notebook_paths_by_ref[
                    asset.path.relative_to(BUILTIN_NOTEBOOK_TEMPLATE_ROOT).with_suffix('').as_posix()
                ] = asset.path
                notebook_paths_by_ref[asset.path.relative_to(BUILTIN_NOTEBOOK_TEMPLATE_ROOT).as_posix()] = asset.path
    else:
        root = Path(path)
        if (root / 'builtin').exists() or (root / 'pipelines').exists():
            notebook_root = root / 'builtin' if (root / 'builtin').exists() else root
            pipeline_root = root / 'pipelines' if (root / 'pipelines').exists() else root
            template_paths = sorted(notebook_root.rglob('*.py')) + sorted(pipeline_root.rglob('*.json'))
            notebook_paths_by_ref = {
                template_path.relative_to(notebook_root).as_posix(): template_path
                for template_path in notebook_root.rglob('*.py')
            }
        else:
            template_paths = sorted(root.rglob('*.py')) + sorted(root.rglob('*.json'))
            notebook_paths_by_ref = {
                template_path.relative_to(root).as_posix(): template_path for template_path in root.rglob('*.py')
            }
    results = []
    for template_path in template_paths:
        results.append(
            {
                'path': str(template_path),
                'issues': validate_template(template_path, notebook_paths_by_ref=notebook_paths_by_ref),
            }
        )
    return results
