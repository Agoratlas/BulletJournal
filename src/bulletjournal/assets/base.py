from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(slots=True)
class BaseAsset:
    asset_type_id: ClassVar[str] = 'generic'
    interactive: ClassVar[bool] = False


def asset_type_id_for_class(asset_type: object) -> str | None:
    if not isinstance(asset_type, type) or not issubclass(asset_type, BaseAsset):
        return None
    candidate = getattr(asset_type, 'asset_type_id', None)
    return candidate if isinstance(candidate, str) and candidate else None


def asset_type_id_for_instance(asset: object) -> str | None:
    if not isinstance(asset, BaseAsset):
        return None
    return asset_type_id_for_class(asset.__class__)
