from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from bulletjournal.assets.collection_utils import collection_child_definition, collection_child_objects
from bulletjournal.domain.errors import InvalidRequestError, NotFoundError


class AssetService:
    def __init__(self, project_service) -> None:
        self.project_service = project_service

    def list_assets_for_node(self, node_id: str) -> list[dict[str, Any]]:
        self.project_service.get_node(node_id)
        state_db = self.project_service.require_project().state_db
        annotations = state_db.list_asset_annotations(node_id)
        return [self._with_annotation(asset, annotations) for asset in state_db.list_asset_heads(node_id=node_id)]

    def get_asset(self, node_id: str, asset_name: str) -> dict[str, Any]:
        self.project_service.get_node(node_id)
        head = self.project_service.require_project().state_db.get_asset_head(node_id, asset_name)
        if head is None:
            raise NotFoundError(f'Unknown asset `{node_id}/{asset_name}`.')
        return self._with_annotation(
            head, self.project_service.require_project().state_db.list_asset_annotations(node_id)
        )

    def image_file(
        self,
        node_id: str,
        asset_name: str,
        *,
        asset_version_id: int,
        collection_child_name: str | None = None,
    ) -> dict[str, Any]:
        head = self.get_asset(node_id, asset_name)
        if head.get('current_asset_version_id') != asset_version_id:
            raise InvalidRequestError('The image asset changed. Refresh it before loading its contents.')
        definition = head.get('definition') or {}
        objects = head.get('objects', [])
        asset_type = head.get('asset_type')
        if asset_type == 'collection':
            if collection_child_name is None or not collection_child_name.strip():
                raise InvalidRequestError('Collection image content requires `collection_child_name`.')
            definition = collection_child_definition(
                node_id=node_id,
                asset_name=asset_name,
                definition=definition,
                child_name=collection_child_name.strip(),
            )
            objects = collection_child_objects(child_definition=definition, parent_objects=objects)
            asset_type = definition.get('asset_type')
        elif collection_child_name is not None:
            raise InvalidRequestError('`collection_child_name` is only supported for collection assets.')
        if asset_type != 'image':
            raise InvalidRequestError('Asset content requires an image asset or an image collection child.')
        reference = definition.get('image')
        if not isinstance(reference, dict) or reference.get('object_role') != 'image':
            raise InvalidRequestError('Image asset is missing its stored image reference.')
        image_object = next(
            (
                item
                for item in objects
                if item['object_role'] == reference['object_role']
                and item['object_index'] == reference.get('object_index', 0)
            ),
            None,
        )
        if image_object is None:
            raise NotFoundError(f'Unknown image object for asset `{node_id}/{asset_name}`.')
        metadata = image_object.get('metadata') or {}
        mime_type = metadata.get('mime_type')
        if mime_type not in {'image/png', 'image/jpeg', 'image/svg+xml'}:
            raise InvalidRequestError('Image object is missing supported image metadata.')
        project = self.project_service.require_project()
        artifact_hash = image_object['artifact_hash']
        project.state_db.touch_artifact_object(artifact_hash)
        lease_id = project.state_db.acquire_object_lease(
            artifact_hash,
            'asset_image',
            str(uuid.uuid4()),
            expires_at=(datetime.now(tz=UTC) + timedelta(hours=1)).isoformat().replace('+00:00', 'Z'),
        )
        try:
            path = project.object_store.load_file_path(artifact_hash, extension=metadata.get('extension'))
        except BaseException:
            project.state_db.release_object_lease(lease_id)
            raise
        return {
            'path': path,
            'mime_type': mime_type,
            'filename': f'{asset_name}{metadata.get("extension") or ""}',
            'lease_id': lease_id,
        }

    @staticmethod
    def _with_annotation(asset: dict[str, Any], annotations: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {**asset, 'annotation': annotations.get(asset['asset_name'], {'is_favorite': False, 'note': ''})}

    def patch_annotation(
        self, node_id: str, asset_name: str, *, is_favorite: bool | None, note: str | None
    ) -> dict[str, Any]:
        self.project_service.get_node(node_id)
        project = self.project_service.require_project()
        project.state_db.patch_asset_annotation(node_id, asset_name, is_favorite=is_favorite, note=note)
        self.project_service.event_service.publish(
            'asset.annotation_updated',
            project_id=project.metadata.project_id,
            graph_version=int(self.project_service.graph().meta['graph_version']),
            payload={'node_id': node_id, 'asset_name': asset_name},
        )
        if is_favorite is not None:
            self.project_service.event_service.publish(
                'dashboard.updated',
                project_id=project.metadata.project_id,
                graph_version=int(self.project_service.graph().meta['graph_version']),
                payload={
                    'dashboard_id': '@favorites',
                    'version': project.state_db.get_favorites_dashboard()['version'],
                },
            )
        return self.get_asset(node_id, asset_name)
