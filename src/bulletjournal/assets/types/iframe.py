from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bulletjournal.assets.base import BaseAsset
from bulletjournal.assets.serialization import SerializedAssetVersion, base_asset_definition


@dataclass(slots=True)
class Iframe(BaseAsset):
    url: str

    asset_type_id = 'iframe'
    interactive = False

    def __post_init__(self) -> None:
        if not isinstance(self.url, str):
            raise TypeError('Iframe assets require a string URL payload.')


def serialize_iframe(
    asset: Iframe,
    *,
    object_store,
    title: str,
    description: str | None,
) -> SerializedAssetVersion:
    del object_store
    modifier_schema: list[dict[str, Any]] = []
    default_modifiers: dict[str, Any] = {}
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
            'iframe_url': asset.url,
        },
        modifier_schema=modifier_schema,
        default_modifiers=default_modifiers,
        objects=[],
    )
