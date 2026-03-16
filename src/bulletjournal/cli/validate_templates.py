from __future__ import annotations

from pathlib import Path

from bulletjournal.templates.registry import builtin_templates
from bulletjournal.templates.validator import validate_template


def validate_templates(path: str | None = None) -> list[dict[str, object]]:
    if path is None:
        template_paths = builtin_templates()
    else:
        root = Path(path)
        template_paths = sorted(root.glob('*.py'))
    results = []
    for template_path in template_paths:
        results.append({'path': str(template_path), 'issues': validate_template(template_path)})
    return results
