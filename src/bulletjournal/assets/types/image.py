from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from PIL import Image as PILImage
from PIL import UnidentifiedImageError

from bulletjournal.assets.base import BaseAsset
from bulletjournal.assets.serialization import SerializedAssetObject, SerializedAssetVersion, base_asset_definition
from bulletjournal.runtime.files import FileSnapshot, _capture, _check_size

DEFAULT_IMAGE_MAX_SIZE_BYTES = 10_000_000


@dataclass(slots=True)
class Image(BaseAsset):
    """Capture a PNG, JPEG, or SVG for display in asset panels and dashboards.

    ``source`` accepts a path or FileSnapshot. ``max_size_bytes`` limits the encoded
    file size (10 MB by default); increase it explicitly for an individual image.
    Capturing is immediate, so the source can be deleted before publishing.
    """

    source: str | Path | FileSnapshot
    alt: str | None = field(default=None, kw_only=True)
    max_size_bytes: int = field(default=DEFAULT_IMAGE_MAX_SIZE_BYTES, kw_only=True)
    _mime_type: str = field(init=False, repr=False)

    asset_type_id = 'image'
    interactive = False

    def __post_init__(self) -> None:
        if isinstance(self.max_size_bytes, bool) or not isinstance(self.max_size_bytes, int):
            raise TypeError('Image max_size_bytes must be a positive integer.')
        if self.max_size_bytes <= 0:
            raise ValueError('Image max_size_bytes must be a positive integer.')
        if self.alt is not None and not isinstance(self.alt, str):
            raise TypeError('Image alt must be a string or None.')
        snapshot = _capture(self.source, max_size_bytes=self.max_size_bytes)
        mime_type, default_extension = _image_format(snapshot.path)
        if snapshot.extension is None:
            snapshot = _capture(snapshot, extension=default_extension, max_size_bytes=self.max_size_bytes)
        self.source = snapshot
        self._mime_type = mime_type


def _image_format(path: Path) -> tuple[str, str]:
    try:
        with PILImage.open(path) as image:
            if image.format not in {'PNG', 'JPEG'}:
                raise ValueError('Image assets support PNG, JPEG, and SVG files.')
            image_format = image.format
            image.verify()
        return ('image/png', '.png') if image_format == 'PNG' else ('image/jpeg', '.jpg')
    except (UnidentifiedImageError, OSError, SyntaxError):
        pass
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, DefusedXmlException, ValueError) as exc:
        raise ValueError('Image assets require a valid PNG, JPEG, or SVG file.') from exc
    if root.tag not in {'svg', '{http://www.w3.org/2000/svg}svg'}:
        raise ValueError('Image assets require a valid PNG, JPEG, or SVG file.')
    return 'image/svg+xml', '.svg'


def serialize_image(
    asset: Image,
    *,
    object_store,
    title: str,
    description: str | None,
) -> SerializedAssetVersion:
    snapshot = asset.source
    if not isinstance(snapshot, FileSnapshot):
        raise TypeError('Image source must be a captured file.')
    _check_size(snapshot.path.stat().st_size, asset.max_size_bytes)
    persisted = object_store.persist_file(snapshot.path, extension=snapshot.extension)
    persisted['mime_type'] = asset._mime_type
    return SerializedAssetVersion(
        asset_type=asset.asset_type_id,
        interactive=False,
        definition={
            **base_asset_definition(
                asset_type=asset.asset_type_id,
                interactive=False,
                title=title,
                description=description,
                modifier_schema=[],
                default_modifiers={},
            ),
            'image': {'object_role': 'image', 'object_index': 0},
            'alt': asset.alt,
        },
        modifier_schema=[],
        default_modifiers={},
        objects=[
            SerializedAssetObject(
                object_role='image',
                persisted=persisted,
                metadata={
                    'extension': snapshot.extension,
                    'mime_type': asset._mime_type,
                    'size_bytes': snapshot.size_bytes,
                },
            )
        ],
    )
