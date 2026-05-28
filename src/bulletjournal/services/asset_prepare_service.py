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
        del panel_context
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

        registration = asset_registration_for_type_id(head.get('asset_type'))
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
        dataset_object = backing_dataset_object(head.get('objects'))
        project.state_db.touch_artifact_object(dataset_object['artifact_hash'])
        dataset_path = project.object_store.load_file_path(str(dataset_object['artifact_hash']))
        payloads, resolved_modifiers = registration.prepare(
            dataset_path=dataset_path,
            definition=head.get('definition') or {},
            default_modifiers=head.get('default_modifiers') or {},
            modifier_overrides=modifier_overrides,
            transient_modifiers=transient_modifiers,
        )
        response = {
            'asset_version_id': int(current_asset_version_id),
            'state': head['state'],
            'resolved_modifiers': resolved_modifiers,
            'override_schema_hash': head.get('override_schema_hash'),
            'payloads': payloads,
            'errors': errors,
        }
        if len(json.dumps(response, ensure_ascii=True).encode('utf-8')) > MAX_PREPARED_RESPONSE_BYTES:
            raise InvalidRequestError('Prepared asset response exceeds the 1 MB cap.')
        return response
