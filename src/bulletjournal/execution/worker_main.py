from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from bulletjournal.domain.enums import ArtifactRole, LineageMode
from bulletjournal.domain.models import Port
from bulletjournal.execution.manifests import RunManifest
from bulletjournal.execution.marimo_adapter import execute_notebook
from bulletjournal.runtime.context import Binding, RuntimeContext, activate_runtime_context


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if not args:
        raise SystemExit('Usage: python -m bulletjournal.execution.worker_main <manifest.json>')
    manifest_path = Path(args[0])
    manifest = RunManifest.from_dict(json.loads(manifest_path.read_text(encoding='utf-8')))
    bindings = {
        name: Binding(
            source_node=value.get('source_node', ''),
            source_artifact=value.get('source_artifact', ''),
            data_type=value['data_type'],
            default=value.get('default'),
            has_default=bool(value.get('has_default', False)),
        )
        for name, value in manifest.bindings.items()
    }
    outputs = {
        name: Port(
            name=name,
            data_type=value['data_type'],
            role=ArtifactRole(value['role']),
            description=value.get('description'),
            kind=value.get('kind', 'value'),
            direction='output',
        )
        for name, value in manifest.outputs.items()
    }
    context = RuntimeContext(
        project_root=Path(manifest.project_root),
        node_id=manifest.node_id,
        run_id=manifest.run_id,
        source_hash=manifest.source_hash,
        lineage_mode=LineageMode(manifest.lineage_mode),
        bindings=bindings,
        outputs=outputs,
    )
    try:
        with activate_runtime_context(context):
            execute_notebook(Path(manifest.notebook_path))
    except Exception as exc:  # noqa: BLE001
        payload = {
            'status': 'error',
            'error': str(exc),
            'traceback': traceback.format_exc(),
            'outputs': context.pushed_outputs,
        }
        sys.stdout.write(json.dumps(payload))
        return 1
    payload = {'status': 'ok', 'outputs': context.pushed_outputs}
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
