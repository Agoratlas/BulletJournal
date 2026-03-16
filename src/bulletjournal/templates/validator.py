from __future__ import annotations

from pathlib import Path

from bulletjournal.parser.interface_parser import parse_notebook_interface


def validate_template(path: Path) -> list[dict[str, object]]:
    interface = parse_notebook_interface(path, node_id=path.stem)
    return [issue.to_dict() for issue in interface.issues]
