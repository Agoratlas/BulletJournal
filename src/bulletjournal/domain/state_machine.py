from __future__ import annotations

from bulletjournal.domain.enums import ArtifactState


def derive_node_state(
    output_states: list[str],
    run_failed: bool = False,
    running: bool = False,
    queued: bool = False,
    validation_failed: bool = False,
) -> str:
    if running:
        return 'running'
    if queued:
        return 'queued'
    if validation_failed:
        return 'error'
    if run_failed:
        return 'error'
    if not output_states:
        return 'idle'
    if all(state == ArtifactState.READY.value for state in output_states):
        return 'ready'
    if any(state == ArtifactState.STALE.value for state in output_states):
        return 'stale'
    if all(state == ArtifactState.PENDING.value for state in output_states):
        return 'pending'
    return 'mixed'
