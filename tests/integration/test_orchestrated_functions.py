from __future__ import annotations

import time

from fastapi.testclient import TestClient

from bulletjournal.api.app import create_app
from bulletjournal.storage.project_fs import init_project_root


def _wait_for_run_status(client: TestClient, run_id: str, expected: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = client.get('/api/v1/project/snapshot').json()
        run = next((entry for entry in snapshot['runs'] if entry['run_id'] == run_id), None)
        if run is not None and run['status'] == expected:
            return
        time.sleep(0.05)
    raise AssertionError(f'Run `{run_id}` did not reach status `{expected}` within 10 seconds.')


def test_orchestrated_notebook_runs_app_function(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    app = create_app(project_path=project_root)
    client = TestClient(app)
    notebook_source = (
        """
import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts


@app.function
def multiply(value: int, factor: int) -> int:
    return value * factor


@app.cell
def _():
    artifacts.push(multiply(6, 7), name="result", data_type=int)
    return


if __name__ == "__main__":
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
""".strip()
        + '\n'
    )

    snapshot = client.get('/api/v1/project/snapshot').json()
    response = client.patch(
        '/api/v1/graph',
        json={
            'graph_version': snapshot['graph']['meta']['graph_version'],
            'operations': [
                {
                    'type': 'add_notebook_node',
                    'node_id': 'function_node',
                    'title': 'Function Node',
                    'source_text': notebook_source,
                }
            ],
        },
    )
    assert response.status_code == 200

    run = client.post(
        '/api/v1/nodes/function_node/run',
        json={'mode': 'run_stale', 'action': 'use_stale'},
    )
    assert run.status_code == 200
    assert run.json()['status'] == 'running'
    _wait_for_run_status(client, run.json()['run_id'], 'succeeded')

    artifact = client.get('/api/v1/artifacts/function_node/result')
    assert artifact.status_code == 200
    assert artifact.json()['state'] == 'ready'
    assert artifact.json()['preview']['repr'] == '42'
