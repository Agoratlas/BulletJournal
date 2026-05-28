from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

import bulletjournal.runtime.assets as runtime_assets
from bulletjournal.domain.enums import LineageMode
from bulletjournal.domain.models import AssetDeclaration
from bulletjournal.runtime.context import RuntimeContext
from bulletjournal.services.asset_prepare_service import AssetPrepareService
from bulletjournal.storage.project_fs import init_project_root


class _FakeProjectService:
    def __init__(self, *, project) -> None:
        self._project = project

    def get_node(self, node_id: str):
        return {'id': node_id}

    def require_project(self):
        return self._project


def test_asset_prepare_service_prepares_interactive_collection_child(tmp_path) -> None:
    project_root = init_project_root(tmp_path / 'project').root
    context = RuntimeContext(
        project_root=project_root,
        node_id='producer',
        run_id='run-collection-prepare',
        source_hash='producer-source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={},
        asset_declarations={
            'table_collection': AssetDeclaration(
                node_id='producer',
                name='table_collection',
                title='Table collection',
                description=None,
                declared_asset_type='collection',
                declaration_index=0,
            )
        },
    )
    collection = runtime_assets.Collection(display_mode='single')
    collection.add_asset(runtime_assets.DataFrame(pd.DataFrame({'left': [1, 2, 3]})), name='left_table')
    collection.add_asset(runtime_assets.Markdown('hello'), name='notes')
    context.finalize_asset_push(
        asset=collection,
        name='table_collection',
        title='Table collection',
        description=None,
        asset_type=runtime_assets.Collection,
    )

    service = AssetPrepareService(
        _FakeProjectService(project=SimpleNamespace(state_db=context.db, object_store=context.object_store))
    )
    response = service.prepare_asset(
        'producer',
        'table_collection',
        asset_version_id=None,
        modifier_overrides={'page': {'index': 0, 'size': 25}},
        transient_modifiers={},
        panel_context={'collection_child_name': 'left_table'},
    )

    assert response['payloads']['table']['kind'] == 'table'
    assert response['payloads']['table']['rows_total'] == 3
    assert response['override_schema_hash'] is not None
    assert response['resolved_modifiers']['page'] == {'index': 0, 'size': 25}
