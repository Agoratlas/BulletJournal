from bulletjournal.domain.enums import ArtifactState
from bulletjournal.domain.state_machine import derive_node_state


def test_derive_node_state_keeps_stale_state_during_orchestrated_queue() -> None:
    assert derive_node_state([ArtifactState.STALE.value]) == 'stale'


def test_derive_node_state_keeps_pending_state_during_orchestrated_run() -> None:
    assert derive_node_state([ArtifactState.PENDING.value]) == 'pending'


def test_derive_node_state_keeps_ready_state_when_outputs_are_ready() -> None:
    assert derive_node_state([ArtifactState.READY.value]) == 'ready'


def test_derive_node_state_prefers_error_over_ready() -> None:
    assert derive_node_state([ArtifactState.READY.value], run_failed=True) == 'error'
