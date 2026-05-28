from __future__ import annotations

import json
from typing import Any

from bulletjournal.assets.prepare_utils import backing_dataset_object
from bulletjournal.assets.registry import asset_registration_for_type_id
from bulletjournal.domain.errors import InvalidRequestError, NotFoundError

MAX_PREPARED_RESPONSE_BYTES = 1_000_000


class AssetPrepareService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service

    def prepare_asset(
        self,
        node_id: str,
        asset_name: str,
        *,
        asset_version_id: int | None,
        modifier_overrides: dict[str, Any],
        transient_modifiers: dict[str, Any],
        panel_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        self.project_service.get_node(node_id)
        head = self.project_service.require_project().state_db.get_asset_head(node_id, asset_name)
        if head is None:
            raise NotFoundError(f'Unknown asset `{node_id}/{asset_name}`.')
        current_asset_version_id = head.get('current_asset_version_id')
        if current_asset_version_id is None or head.get('definition') is None:
            raise InvalidRequestError(f'Asset `{node_id}/{asset_name}` has not been produced yet.')
        if not isinstance(modifier_overrides, dict):
            raise InvalidRequestError('modifier_overrides must be an object.')
        if not isinstance(transient_modifiers, dict):
            raise InvalidRequestError('transient_modifiers must be an object.')

        definition = head.get('definition') or {}
        default_modifiers = head.get('default_modifiers') or {}
        objects = head.get('objects')
        override_schema_hash = head.get('override_schema_hash')
        registration = asset_registration_for_type_id(head.get('asset_type'))
        if head.get('asset_type') == 'collection':
            registration, definition, default_modifiers, objects, override_schema_hash = (
                self._collection_child_prepare_target(
                    node_id=node_id,
                    asset_name=asset_name,
                    definition=definition,
                    objects=objects,
                    panel_context=panel_context,
                )
            )
        if registration is None or registration.prepare is None:
            raise InvalidRequestError(
                'Asset prepare is only supported for interactive dataframe, histogram, '
                'pie chart, and scatter plot assets in this release.'
            )

        errors: list[dict[str, str]] = []
        if asset_version_id is not None and asset_version_id != current_asset_version_id:
            errors.append(
                {
                    'code': 'asset_version_mismatch',
                    'message': (
                        f'Asset `{node_id}/{asset_name}` moved from version {asset_version_id} '
                        f'to version {current_asset_version_id}.'
                    ),
                }
            )

        project = self.project_service.require_project()
        dataset_object = backing_dataset_object(objects)
        project.state_db.touch_artifact_object(dataset_object['artifact_hash'])
        dataset_path = project.object_store.load_file_path(str(dataset_object['artifact_hash']))
        payloads, resolved_modifiers = registration.prepare(
            dataset_path=dataset_path,
            definition=definition,
            default_modifiers=default_modifiers,
            modifier_overrides=modifier_overrides,
            transient_modifiers=transient_modifiers,
        )
        response = {
            'asset_version_id': int(current_asset_version_id),
            'state': head['state'],
            'resolved_modifiers': resolved_modifiers,
            'override_schema_hash': override_schema_hash,
            'payloads': payloads,
            'errors': errors,
        }
        if len(json.dumps(response, ensure_ascii=True).encode('utf-8')) > MAX_PREPARED_RESPONSE_BYTES:
            raise InvalidRequestError('Prepared asset response exceeds the 1 MB cap.')
        return response

    def _collection_child_prepare_target(
        self,
        *,
        node_id: str,
        asset_name: str,
        definition: dict[str, Any],
        objects: Any,
        panel_context: dict[str, Any] | None,
    ) -> tuple[Any, dict[str, Any], dict[str, Any], list[dict[str, Any]], str | None]:
        if not isinstance(panel_context, dict):
            raise InvalidRequestError(
                f'Collection asset `{node_id}/{asset_name}` requires '
                '`panel_context.collection_child_name` for interactive prepare.'
            )
        child_name = panel_context.get('collection_child_name')
        if not isinstance(child_name, str) or not child_name.strip():
            raise InvalidRequestError(
                f'Collection asset `{node_id}/{asset_name}` requires '
                '`panel_context.collection_child_name` for interactive prepare.'
            )
        child_definition = self._collection_child_definition(
            node_id=node_id,
            asset_name=asset_name,
            definition=definition,
            child_name=child_name.strip(),
        )
        registration = asset_registration_for_type_id(child_definition.get('asset_type'))
        child_default_modifiers = child_definition.get('default_modifiers')
        if not isinstance(child_default_modifiers, dict):
            child_default_modifiers = child_definition.get('modifier_defaults')
        if not isinstance(child_default_modifiers, dict):
            child_default_modifiers = {}
        child_objects = self._collection_child_objects(child_definition=child_definition, parent_objects=objects)
        child_override_schema_hash = child_definition.get('override_schema_hash')
        return (
            registration,
            child_definition,
            child_default_modifiers,
            child_objects,
            child_override_schema_hash if isinstance(child_override_schema_hash, str) else None,
        )

    @staticmethod
    def _collection_child_definition(
        *,
        node_id: str,
        asset_name: str,
        definition: dict[str, Any],
        child_name: str,
    ) -> dict[str, Any]:
        children = definition.get('children')
        if not isinstance(children, list):
            raise InvalidRequestError(
                f'Collection asset `{node_id}/{asset_name}` does not include any child definitions.'
            )
        for child in children:
            if isinstance(child, dict) and child.get('name') == child_name:
                return child
        raise InvalidRequestError(f'Collection asset `{node_id}/{asset_name}` does not contain child `{child_name}`.')

    @staticmethod
    def _collection_child_objects(*, child_definition: dict[str, Any], parent_objects: Any) -> list[dict[str, Any]]:
        if not isinstance(parent_objects, list):
            raise InvalidRequestError('Collection asset objects are missing.')
        parent_objects_by_ref = {
            (item.get('object_role'), item.get('object_index')): item
            for item in parent_objects
            if isinstance(item, dict)
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
