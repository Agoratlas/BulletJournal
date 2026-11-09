from __future__ import annotations

from typing import Any

from bulletjournal.domain.errors import InvalidRequestError


def collection_child_definition(
    *,
    node_id: str,
    asset_name: str,
    definition: dict[str, Any],
    child_name: str,
) -> dict[str, Any]:
    children = definition.get('children')
    if not isinstance(children, list):
        raise InvalidRequestError(f'Collection asset `{node_id}/{asset_name}` does not include any child definitions.')
    for child in children:
        if isinstance(child, dict) and child.get('name') == child_name:
            return child
    raise InvalidRequestError(f'Collection asset `{node_id}/{asset_name}` does not contain child `{child_name}`.')


def collection_child_objects(*, child_definition: dict[str, Any], parent_objects: Any) -> list[dict[str, Any]]:
    if not isinstance(parent_objects, list):
        raise InvalidRequestError('Collection asset objects are missing.')
    parent_objects_by_ref = {
        (item.get('object_role'), item.get('object_index')): item for item in parent_objects if isinstance(item, dict)
    }
    child_object_refs = child_definition.get('objects')
    if not isinstance(child_object_refs, list):
        child_object_refs = []
        if isinstance(child_definition.get('object_role'), str):
            child_object_refs.append(
                {
                    'object_role': child_definition.get('object_role'),
                    'object_index': child_definition.get('object_index', 0),
                }
            )
        dataset_binding = child_definition.get('dataset_binding')
        if isinstance(dataset_binding, dict) and isinstance(dataset_binding.get('object_role'), str):
            child_object_refs.append(
                {
                    'object_role': dataset_binding.get('object_role'),
                    'object_index': dataset_binding.get('object_index', 0),
                }
            )
    resolved_objects: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, int]] = set()
    for object_ref in child_object_refs:
        if not isinstance(object_ref, dict):
            continue
        object_role = object_ref.get('object_role')
        raw_index = object_ref.get('object_index', 0)
        if not isinstance(object_role, str):
            continue
        object_index = raw_index if isinstance(raw_index, int) and not isinstance(raw_index, bool) else 0
        key = (object_role, object_index)
        if key in seen_refs:
            continue
        seen_refs.add(key)
        parent_object = parent_objects_by_ref.get(key)
        if not isinstance(parent_object, dict) or not parent_object.get('artifact_hash'):
            raise InvalidRequestError(
                f'Collection child object `{object_role}[{object_index}]` is missing from the stored asset version.'
            )
        resolved_objects.append(parent_object)
    return resolved_objects
