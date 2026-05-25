from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bulletjournal.assets.runtime_types import BaseAsset, DataFrameAsset, MarkdownAsset, asset_type_id_for_instance
from bulletjournal.storage.object_store import ObjectStore


@dataclass(slots=True)
class SerializedAssetObject:
    object_role: str
    persisted: dict[str, Any]
    object_index: int = 0
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class SerializedAssetVersion:
    asset_type: str
    interactive: bool
    definition: dict[str, Any]
    modifier_schema: list[dict[str, Any]]
    default_modifiers: dict[str, Any]
    objects: list[SerializedAssetObject]


def serialize_asset(
    asset: BaseAsset,
    *,
    object_store: ObjectStore,
    description: str | None,
) -> SerializedAssetVersion:
    asset_type = asset_type_id_for_instance(asset)
    if asset_type is None:
        raise TypeError(f'Unsupported asset instance `{type(asset).__name__}`.')
    modifier_schema: list[dict[str, Any]] = []
    default_modifiers: dict[str, Any] = {}
    base_definition = {
        'asset_type': asset_type,
        'interactive': bool(getattr(asset.__class__, 'interactive', False)),
        'display_title': True,
        'description': description,
        'supports_table_view': False,
        'modifier_defaults': default_modifiers,
        'modifier_schema': modifier_schema,
        'interaction_bindings': [],
        'data_dependencies': [],
    }
    if isinstance(asset, MarkdownAsset):
        return SerializedAssetVersion(
            asset_type=asset_type,
            interactive=False,
            definition={
                **base_definition,
                'markdown_text': asset.text,
            },
            modifier_schema=modifier_schema,
            default_modifiers=default_modifiers,
            objects=[],
        )
    if isinstance(asset, DataFrameAsset):
        persisted = object_store.persist_value(asset.dataframe, 'pandas.DataFrame')
        return SerializedAssetVersion(
            asset_type=asset_type,
            interactive=True,
            definition={
                **base_definition,
                'interactive': True,
                'supports_table_view': True,
                'data_dependencies': ['backing_dataset'],
                'table_columns': [str(column) for column in asset.dataframe.columns],
                'row_count': int(asset.dataframe.shape[0]),
                'object_role': 'backing_dataset',
            },
            modifier_schema=modifier_schema,
            default_modifiers=default_modifiers,
            objects=[SerializedAssetObject(object_role='backing_dataset', persisted=persisted)],
        )
    raise TypeError(f'Unsupported asset instance `{type(asset).__name__}`.')
