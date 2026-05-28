from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bulletjournal.assets.base import BaseAsset
from bulletjournal.assets.serialization import SerializedAssetVersion, base_asset_definition


@dataclass(slots=True)
class Markdown(BaseAsset):
    text: str

    asset_type_id = 'markdown'
    interactive = False

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError('Markdown assets require a string payload.')


def serialize_markdown(
    asset: Markdown,
    *,
    object_store,
    title: str,
    description: str | None,
) -> SerializedAssetVersion:
    del object_store, title
    modifier_schema: list[dict[str, Any]] = []
    default_modifiers: dict[str, Any] = {}
    return SerializedAssetVersion(
        asset_type=asset.asset_type_id,
        interactive=False,
        definition={
            **base_asset_definition(
                asset_type=asset.asset_type_id,
                interactive=False,
                description=description,
                modifier_schema=modifier_schema,
                default_modifiers=default_modifiers,
            ),
            'markdown_text': asset.text,
        },
        modifier_schema=modifier_schema,
        default_modifiers=default_modifiers,
        objects=[],
    )
