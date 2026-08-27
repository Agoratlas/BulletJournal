from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from bulletjournal.assets.prepare_utils import ALLOWED_PAGE_SIZES, backing_dataset_object
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
        persisted_override_schema_hash: str | None,
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
        modifier_schema = head.get('modifier_schema') or []
        objects = head.get('objects')
        override_schema_hash = head.get('override_schema_hash')
        registration = asset_registration_for_type_id(head.get('asset_type'))
        if head.get('asset_type') == 'collection':
            registration, definition, default_modifiers, modifier_schema, objects, override_schema_hash = (
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

        validating_persisted_overrides = (
            isinstance(persisted_override_schema_hash, str)
            and persisted_override_schema_hash != ''
            and isinstance(override_schema_hash, str)
            and persisted_override_schema_hash != override_schema_hash
        )
        if validating_persisted_overrides:
            try:
                self._validate_modifier_overrides(modifier_overrides, modifier_schema)
            except InvalidRequestError as exc:
                return self._override_incompatible_response(
                    current_asset_version_id=int(current_asset_version_id),
                    state=str(head['state']),
                    override_schema_hash=override_schema_hash,
                    errors=errors,
                    exc=exc,
                )

        project = self.project_service.require_project()
        dataset_object = backing_dataset_object(objects)
        project.state_db.touch_artifact_object(dataset_object['artifact_hash'])
        lease_id = project.state_db.acquire_object_lease(
            str(dataset_object['artifact_hash']),
            'asset_prepare',
            str(uuid.uuid4()),
            expires_at=(datetime.now(tz=UTC) + timedelta(hours=1)).isoformat().replace('+00:00', 'Z'),
        )
        dataset_path = project.object_store.load_file_path(str(dataset_object['artifact_hash']))
        try:
            payloads, resolved_modifiers = registration.prepare(
                dataset_path=dataset_path,
                definition=definition,
                default_modifiers=default_modifiers,
                modifier_overrides=modifier_overrides,
                transient_modifiers=transient_modifiers,
            )
        except InvalidRequestError as exc:
            if validating_persisted_overrides:
                return self._override_incompatible_response(
                    current_asset_version_id=int(current_asset_version_id),
                    state=str(head['state']),
                    override_schema_hash=override_schema_hash,
                    errors=errors,
                    exc=exc,
                )
            raise
        finally:
            project.state_db.release_object_lease(lease_id)
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

    def prepare_artifact_dataframe(
        self,
        node_id: str,
        artifact_name: str,
        *,
        artifact_version_id: int | None,
        modifier_overrides: dict[str, Any],
        transient_modifiers: dict[str, Any],
    ) -> dict[str, Any]:
        head = self.project_service.require_project().state_db.get_artifact_head(node_id, artifact_name)
        if head is None:
            raise NotFoundError(f'Unknown artifact `{node_id}/{artifact_name}`.')
        current_version_id = head.get('current_version_id')
        if current_version_id is None or head.get('artifact_hash') is None:
            raise InvalidRequestError(f'Artifact `{node_id}/{artifact_name}` has not been produced yet.')
        if head.get('data_type') != 'pandas.DataFrame':
            raise InvalidRequestError(f'Artifact `{node_id}/{artifact_name}` is not a DataFrame.')
        if not isinstance(modifier_overrides, dict) or not isinstance(transient_modifiers, dict):
            raise InvalidRequestError('DataFrame modifiers must be objects.')

        from bulletjournal.assets.types.dataframe import prepare_dataframe

        project = self.project_service.require_project()
        artifact_hash = str(head['artifact_hash'])
        preview = head.get('preview')
        dataset_path = (
            None
            if isinstance(preview, dict) and preview.get('kind') == 'empty'
            else project.object_store.load_file_path(artifact_hash)
        )
        project.state_db.touch_artifact_object(artifact_hash)
        lease_id = project.state_db.acquire_object_lease(
            artifact_hash,
            'artifact_prepare',
            str(uuid.uuid4()),
            expires_at=(datetime.now(tz=UTC) + timedelta(hours=1)).isoformat().replace('+00:00', 'Z'),
        )
        try:
            payloads, resolved_modifiers = prepare_dataframe(
                dataset_path=dataset_path,
                definition={},
                default_modifiers={
                    'page': {'index': 0, 'size': 25},
                    'sort': [],
                    'filters': [],
                    'highlights': [],
                },
                modifier_overrides=modifier_overrides,
                transient_modifiers=transient_modifiers,
            )
        finally:
            project.state_db.release_object_lease(lease_id)
        errors: list[dict[str, str]] = []
        if artifact_version_id is not None and artifact_version_id != current_version_id:
            errors.append(
                {
                    'code': 'artifact_version_mismatch',
                    'message': (
                        f'Artifact `{node_id}/{artifact_name}` moved from version '
                        f'{artifact_version_id} to version {current_version_id}.'
                    ),
                }
            )
        response = {
            'asset_version_id': int(current_version_id),
            'state': head['state'],
            'resolved_modifiers': resolved_modifiers,
            'override_schema_hash': None,
            'payloads': payloads,
            'errors': errors,
        }
        if len(json.dumps(response, ensure_ascii=True).encode('utf-8')) > MAX_PREPARED_RESPONSE_BYTES:
            raise InvalidRequestError('Prepared artifact response exceeds the 1 MB cap.')
        return response

    def _collection_child_prepare_target(
        self,
        *,
        node_id: str,
        asset_name: str,
        definition: dict[str, Any],
        objects: Any,
        panel_context: dict[str, Any] | None,
    ) -> tuple[Any, dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str | None]:
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
        child_modifier_schema = child_definition.get('modifier_schema')
        if not isinstance(child_modifier_schema, list):
            child_modifier_schema = []
        child_objects = self._collection_child_objects(child_definition=child_definition, parent_objects=objects)
        child_override_schema_hash = child_definition.get('override_schema_hash')
        return (
            registration,
            child_definition,
            child_default_modifiers,
            child_modifier_schema,
            child_objects,
            child_override_schema_hash if isinstance(child_override_schema_hash, str) else None,
        )

    @staticmethod
    def _override_incompatible_response(
        *,
        current_asset_version_id: int,
        state: str,
        override_schema_hash: str | None,
        errors: list[dict[str, str]],
        exc: InvalidRequestError,
    ) -> dict[str, Any]:
        return {
            'asset_version_id': current_asset_version_id,
            'state': state,
            'resolved_modifiers': {},
            'override_schema_hash': override_schema_hash,
            'payloads': {},
            'errors': [
                *errors,
                {
                    'code': 'override_incompatible',
                    'message': str(exc),
                },
            ],
        }

    @classmethod
    def _validate_modifier_overrides(
        cls,
        modifier_overrides: dict[str, Any],
        modifier_schema: list[dict[str, Any]],
    ) -> None:
        if not isinstance(modifier_overrides, dict):
            raise InvalidRequestError('modifier_overrides must be an object.')
        schema_by_id = {
            entry['id']: entry
            for entry in modifier_schema
            if isinstance(entry, dict) and isinstance(entry.get('id'), str)
        }
        for key, value in modifier_overrides.items():
            schema_entry = schema_by_id.get(key)
            if schema_entry is None:
                raise InvalidRequestError(f'Unknown modifier `{key}`.')
            cls._validate_modifier_override_value(key=key, value=value, schema_entry=schema_entry)

    @classmethod
    def _validate_modifier_override_value(cls, *, key: str, value: Any, schema_entry: dict[str, Any]) -> None:
        kind = schema_entry.get('kind')
        if kind == 'sort':
            cls._validate_sort_override(key=key, value=value, schema_entry=schema_entry)
            return
        if kind == 'filters':
            cls._validate_filters_override(key=key, value=value, schema_entry=schema_entry)
            return
        if kind == 'highlights':
            cls._validate_highlights_override(key=key, value=value, schema_entry=schema_entry)
            return
        if kind == 'enum':
            cls._validate_enum_override(key=key, value=value, schema_entry=schema_entry)
            return
        default_value = schema_entry.get('default_value')
        cls._validate_partial_value_shape(label=key, value=value, default_value=default_value)

    @classmethod
    def _validate_sort_override(cls, *, key: str, value: Any, schema_entry: dict[str, Any]) -> None:
        if not isinstance(value, list):
            raise InvalidRequestError(f'modifier_overrides.{key} must be an array.')
        if len(value) > 1:
            raise InvalidRequestError('Only one active sort key is supported in this release.')
        column_ids = {
            column['id']
            for column in schema_entry.get('columns', [])
            if isinstance(column, dict) and isinstance(column.get('id'), str)
        }
        for entry in value:
            if not isinstance(entry, dict):
                raise InvalidRequestError(f'modifier_overrides.{key} entries must be objects.')
            column = entry.get('column')
            direction = entry.get('direction')
            if not isinstance(column, str) or column not in column_ids:
                raise InvalidRequestError(f'Unknown sort column `{column}`.')
            if direction not in {'asc', 'desc'}:
                raise InvalidRequestError('Sort direction must be `asc` or `desc`.')

    @classmethod
    def _validate_filters_override(cls, *, key: str, value: Any, schema_entry: dict[str, Any]) -> None:
        if not isinstance(value, list):
            raise InvalidRequestError(f'modifier_overrides.{key} must be an array.')
        columns_by_id = {
            column['id']: column
            for column in schema_entry.get('columns', [])
            if isinstance(column, dict) and isinstance(column.get('id'), str)
        }
        seen_columns: set[str] = set()
        for entry in value:
            if not isinstance(entry, dict):
                raise InvalidRequestError(f'modifier_overrides.{key} entries must be objects.')
            column = entry.get('column')
            if not isinstance(column, str) or column not in columns_by_id:
                raise InvalidRequestError(f'Unknown filter column `{column}`.')
            if column in seen_columns:
                raise InvalidRequestError(f'Only one active filter per column is supported for `{column}`.')
            seen_columns.add(column)
            kind = entry.get('kind')
            allowed_kinds = columns_by_id[column].get('filter_kinds')
            if kind not in {'range', 'value', 'regex'}:
                raise InvalidRequestError('Filter kind must be `range`, `value`, or `regex`.')
            if isinstance(allowed_kinds, list) and kind not in allowed_kinds:
                raise InvalidRequestError(f'Filter kind `{kind}` is not supported for column `{column}`.')
            if kind == 'range':
                lower = entry.get('lower')
                upper = entry.get('upper')
                if lower is None and upper is None:
                    raise InvalidRequestError(f'Range filter `{column}` must define `lower`, `upper`, or both.')
                if not cls._is_scalar_or_none(lower) or not cls._is_scalar_or_none(upper):
                    raise InvalidRequestError(f'Range filter `{column}` bounds must be scalar values.')
                continue
            if kind == 'value':
                values = entry.get('values', [])
                if not isinstance(values, list):
                    raise InvalidRequestError(f'Value filter `{column}` must define `values` as an array.')
                if any(not cls._is_scalar(item) for item in values):
                    raise InvalidRequestError(f'Value filter `{column}` must contain only scalar values.')
                include_null = entry.get('include_null', False)
                if not isinstance(include_null, bool):
                    raise InvalidRequestError(f'Value filter `{column}` include_null must be boolean.')
                if not values and not include_null:
                    raise InvalidRequestError(
                        f'Value filter `{column}` must define at least one value or include nulls.'
                    )
                continue
            pattern = entry.get('pattern')
            if not isinstance(pattern, str) or not pattern:
                raise InvalidRequestError(f'Regex filter `{column}` must define a non-empty `pattern`.')
            case_sensitive = entry.get('case_sensitive', False)
            if not isinstance(case_sensitive, bool):
                raise InvalidRequestError(f'Regex filter `{column}` case_sensitive must be boolean.')

    @classmethod
    def _validate_highlights_override(cls, *, key: str, value: Any, schema_entry: dict[str, Any]) -> None:
        if not isinstance(value, list):
            raise InvalidRequestError(f'modifier_overrides.{key} must be an array.')
        columns_by_id = {
            column['id']: column
            for column in schema_entry.get('columns', [])
            if isinstance(column, dict) and isinstance(column.get('id'), str)
        }
        for entry in value:
            if not isinstance(entry, dict):
                raise InvalidRequestError(f'modifier_overrides.{key} entries must be objects.')
            column = entry.get('column')
            if not isinstance(column, str) or column not in columns_by_id:
                raise InvalidRequestError(f'Unknown highlight column `{column}`.')
            kind = entry.get('kind')
            allowed_kinds = columns_by_id[column].get('filter_kinds')
            if kind not in {'range', 'value', 'regex'}:
                raise InvalidRequestError('Highlight kind must be `range`, `value`, or `regex`.')
            if isinstance(allowed_kinds, list) and kind not in allowed_kinds:
                raise InvalidRequestError(f'Highlight kind `{kind}` is not supported for column `{column}`.')
            if entry.get('highlight_scope', 'cell') not in {'cell', 'row'}:
                raise InvalidRequestError('Highlight scope must be `cell` or `row`.')
            color = entry.get('highlight_color')
            if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
                raise InvalidRequestError('Highlight color must be a six-digit hex color such as `#ff0000`.')
            cls._validate_filters_override(key=key, value=[entry], schema_entry={**schema_entry, 'kind': 'filters'})

    @classmethod
    def _validate_enum_override(cls, *, key: str, value: Any, schema_entry: dict[str, Any]) -> None:
        allowed_values = cls._enum_option_values(schema_entry.get('options'))
        if not cls._is_scalar(value) or value not in allowed_values:
            allowed = ', '.join(repr(item) for item in allowed_values)
            raise InvalidRequestError(f'modifier_overrides.{key} must be one of: {allowed}.')

    @classmethod
    def _validate_partial_value_shape(cls, *, label: str, value: Any, default_value: Any) -> None:
        if default_value is None:
            if value is None or cls._is_scalar(value) or isinstance(value, list | dict):
                return
            raise InvalidRequestError(f'modifier_overrides.{label} has an invalid value.')
        if isinstance(default_value, bool):
            if not isinstance(value, bool):
                raise InvalidRequestError(f'modifier_overrides.{label} must be boolean.')
            return
        if isinstance(default_value, int) and not isinstance(default_value, bool):
            if not isinstance(value, int) or isinstance(value, bool):
                raise InvalidRequestError(f'modifier_overrides.{label} must be an integer.')
            if label.endswith('.size') and value not in ALLOWED_PAGE_SIZES:
                allowed = ', '.join(str(size) for size in sorted(ALLOWED_PAGE_SIZES))
                raise InvalidRequestError(f'Page size must be one of: {allowed}.')
            if label.endswith('.index') and value < 0:
                raise InvalidRequestError('Page index must be a zero-based integer.')
            return
        if isinstance(default_value, float):
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise InvalidRequestError(f'modifier_overrides.{label} must be numeric.')
            return
        if isinstance(default_value, str):
            if not isinstance(value, str):
                raise InvalidRequestError(f'modifier_overrides.{label} must be a string.')
            return
        if isinstance(default_value, list):
            if not isinstance(value, list):
                raise InvalidRequestError(f'modifier_overrides.{label} must be an array.')
            return
        if not isinstance(default_value, dict):
            return
        if not isinstance(value, dict):
            raise InvalidRequestError(f'modifier_overrides.{label} must be an object.')
        unexpected_keys = sorted(set(value) - set(default_value))
        if unexpected_keys:
            formatted = ', '.join(f'`{item}`' for item in unexpected_keys)
            raise InvalidRequestError(f'modifier_overrides.{label} contains unknown fields: {formatted}.')
        for child_key, child_value in value.items():
            cls._validate_partial_value_shape(
                label=f'{label}.{child_key}',
                value=child_value,
                default_value=default_value.get(child_key),
            )

    @staticmethod
    def _enum_option_values(options: Any) -> list[Any]:
        if not isinstance(options, list):
            return []
        values: list[Any] = []
        for option in options:
            if isinstance(option, dict) and 'value' in option and isinstance(option['value'], str | int | float | bool):
                values.append(option['value'])
                continue
            if isinstance(option, str | int | float | bool):
                values.append(option)
        return values

    @staticmethod
    def _is_scalar(value: Any) -> bool:
        return isinstance(value, str | int | float | bool)

    @classmethod
    def _is_scalar_or_none(cls, value: Any) -> bool:
        return value is None or cls._is_scalar(value)

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
