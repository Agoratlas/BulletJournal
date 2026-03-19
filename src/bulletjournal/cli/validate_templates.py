from __future__ import annotations

from pathlib import Path

from bulletjournal.templates.registry import BUILTIN_PROVIDER, builtin_pipeline_templates, builtin_templates
from bulletjournal.templates.validator import BUILTIN_NOTEBOOK_TEMPLATE_ROOT, validate_template


def validate_templates(path: str | None = None) -> list[dict[str, object]]:
    if path is None:
        template_paths = [*builtin_templates(), *builtin_pipeline_templates()]
        notebook_paths_by_ref = {
            f'{BUILTIN_PROVIDER}/{template_path.relative_to(BUILTIN_NOTEBOOK_TEMPLATE_ROOT).with_suffix("").as_posix()}': template_path
            for template_path in builtin_templates()
        }
        for template_path in builtin_templates():
            notebook_paths_by_ref[template_path.relative_to(BUILTIN_NOTEBOOK_TEMPLATE_ROOT).with_suffix('').as_posix()] = template_path
            notebook_paths_by_ref[template_path.relative_to(BUILTIN_NOTEBOOK_TEMPLATE_ROOT).as_posix()] = template_path
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
                template_path.relative_to(root).as_posix(): template_path
                for template_path in root.rglob('*.py')
            }
    results = []
    for template_path in template_paths:
        results.append({'path': str(template_path), 'issues': validate_template(template_path, notebook_paths_by_ref=notebook_paths_by_ref)})
    return results
