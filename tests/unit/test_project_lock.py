import multiprocessing

from bulletjournal.storage.project_lock import ProjectLock


def _try_exclusive(path, queue) -> None:
    try:
        with ProjectLock(path).exclusive(timeout=0.1):
            queue.put('acquired')
    except TimeoutError:
        queue.put('blocked')


def test_project_lock_is_reentrant_and_blocks_another_process(tmp_path) -> None:
    lock_path = tmp_path / 'metadata' / 'project.lock'
    lock = ProjectLock(lock_path)
    context = multiprocessing.get_context('spawn')
    queue = context.Queue()

    with lock.exclusive(), lock.exclusive(), lock.shared():
        process = context.Process(target=_try_exclusive, args=(lock_path, queue))
        process.start()
        process.join(timeout=5)

    assert process.exitcode == 0
    assert queue.get(timeout=1) == 'blocked'
