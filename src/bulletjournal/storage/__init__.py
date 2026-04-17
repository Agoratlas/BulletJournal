from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bulletjournal.storage.graph_store import GraphStore
    from bulletjournal.storage.object_store import ObjectStore
    from bulletjournal.storage.project_fs import ProjectPaths
    from bulletjournal.storage.state_db import StateDB


def __getattr__(name: str):
    if name == 'GraphStore':
        from bulletjournal.storage.graph_store import GraphStore

        return GraphStore
    if name == 'ObjectStore':
        from bulletjournal.storage.object_store import ObjectStore

        return ObjectStore
    if name == 'ProjectPaths':
        from bulletjournal.storage.project_fs import ProjectPaths

        return ProjectPaths
    if name == 'StateDB':
        from bulletjournal.storage.state_db import StateDB

        return StateDB
    if name == 'init_project_root':
        from bulletjournal.storage.project_fs import init_project_root

        return init_project_root
    if name == 'is_project_root':
        from bulletjournal.storage.project_fs import is_project_root

        return is_project_root
    if name == 'require_project_root':
        from bulletjournal.storage.project_fs import require_project_root

        return require_project_root
    raise AttributeError(name)


__all__ = [
    'GraphStore',
    'ObjectStore',
    'ProjectPaths',
    'StateDB',
    'init_project_root',
    'is_project_root',
    'require_project_root',
]
