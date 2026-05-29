from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bulletjournal.assets.base import BaseAsset
from bulletjournal.assets.serialization import SerializedAssetObject, SerializedAssetVersion, base_asset_definition
from bulletjournal.domain.hashing import hash_json


@dataclass(slots=True)
class _CollectionEntry:
    asset: BaseAsset
    name: str
    title: str


@dataclass(slots=True)
class Collection(BaseAsset):
    display_mode: str = 'all'
    _children: list[_CollectionEntry] = field(default_factory=list, init=False, repr=False)

    asset_type_id = 'collection'
    interactive = False

    def __post_init__(self) -> None:
        if not isinstance(self.display_mode, str) or self.display_mode not in {'all', 'single'}:
            raise ValueError("Collection display_mode must be either 'all' or 'single'.")

    def add_asset(self, asset: BaseAsset, name: str | None = None, title: str | None = None) -> None:
        if not isinstance(asset, BaseAsset):
            raise TypeError('Collection children must be BulletJournal asset instances.')
        if isinstance(asset, Collection):
            raise TypeError('Collections cannot contain other collections.')
        resolved_name = self._resolve_child_name(name)
        resolved_title = self._resolve_child_title(title)
        if any(entry.name == resolved_name for entry in self._children):
            raise ValueError(f'Collection already contains a child named `{resolved_name}`.')
        self._children.append(_CollectionEntry(asset=asset, name=resolved_name, title=resolved_title))

    def _resolve_child_name(self, name: str | None) -> str:
        if name is not None:
            if not isinstance(name, str):
                raise TypeError('Collection child names must be strings.')
            resolved = name.strip()
            if not resolved:
                raise ValueError('Collection child names must not be empty.')
            return resolved
        index = 1
        existing = {entry.name for entry in self._children}
        while True:
            candidate = f'asset_{index}'
            if candidate not in existing:
                return candidate
            index += 1

    def _resolve_child_title(self, title: str | None) -> str:
        if title is not None:
            if not isinstance(title, str):
                raise TypeError('Collection child titles must be strings.')
            resolved = title.strip()
            if not resolved:
                raise ValueError('Collection child titles must not be empty.')
            return resolved
        return f'Asset {len(self._children) + 1}'


def serialize_collection(
    asset: Collection,
    *,
    object_store,
    title: str,
    description: str | None,
) -> SerializedAssetVersion:
    from bulletjournal.assets.registry import serialize_asset

    modifier_schema: list[dict[str, Any]] = []
    default_modifiers: dict[str, Any] = {}
    next_object_index_by_role: dict[str, int] = {}
    children: list[dict[str, Any]] = []
    objects: list[SerializedAssetObject] = []

    for entry in asset._children:
        child_name = entry.name
        child_title = entry.title
        serialized_child = serialize_asset(
            entry.asset,
            object_store=object_store,
            title=child_title,
            description=None,
        )
        if serialized_child.asset_type == Collection.asset_type_id:
            raise TypeError('Collections cannot contain other collections.')

        object_index_map: dict[tuple[str, int], int] = {}
        child_object_refs: list[dict[str, Any]] = []
        for item in serialized_child.objects:
            object_index = next_object_index_by_role.get(item.object_role, 0)
            next_object_index_by_role[item.object_role] = object_index + 1
            object_index_map[(item.object_role, item.object_index)] = object_index
            objects.append(
                SerializedAssetObject(
                    object_role=item.object_role,
                    persisted=item.persisted,
                    object_index=object_index,
                    metadata=item.metadata,
                )
            )
            child_object_refs.append(
                {
                    'object_role': item.object_role,
                    'object_index': object_index,
                    'metadata': item.metadata,
                }
            )

        children.append(
            {
                'name': child_name,
                'title': child_title,
                'description': None,
                **_remap_object_indexes(serialized_child.definition, object_index_map),
                'modifier_schema': serialized_child.modifier_schema,
                'default_modifiers': serialized_child.default_modifiers,
                'override_schema_hash': hash_json(serialized_child.modifier_schema),
                'objects': child_object_refs,
            }
        )

    return SerializedAssetVersion(
        asset_type=asset.asset_type_id,
        interactive=False,
        definition={
            **base_asset_definition(
                asset_type=asset.asset_type_id,
                interactive=False,
                title=title,
                description=description,
                modifier_schema=modifier_schema,
                default_modifiers=default_modifiers,
            ),
            'display_mode_default': asset.display_mode,
            'children': children,
        },
        modifier_schema=modifier_schema,
        default_modifiers=default_modifiers,
        objects=objects,
    )


def _remap_object_indexes(value: Any, object_index_map: dict[tuple[str, int], int]) -> Any:
    if isinstance(value, list):
        return [_remap_object_indexes(item, object_index_map) for item in value]
    if not isinstance(value, dict):
        return value

    remapped = {key: _remap_object_indexes(item, object_index_map) for key, item in value.items()}
    object_role = remapped.get('object_role')
    if not isinstance(object_role, str):
        return remapped
    raw_index = remapped.get('object_index', 0)
    object_index = raw_index if isinstance(raw_index, int) and not isinstance(raw_index, bool) and raw_index >= 0 else 0
    mapped_index = object_index_map.get((object_role, object_index))
    if mapped_index is None:
        return remapped
    remapped['object_index'] = mapped_index
    return remapped
