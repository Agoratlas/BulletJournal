import gc
import stat
from types import SimpleNamespace

import pytest
from PIL import Image as PILImage

from bulletjournal.assets.registry import serialize_asset
from bulletjournal.runtime import assets, files
from bulletjournal.storage.object_store import ObjectStore
from bulletjournal.storage.project_fs import init_project_root

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="20"><rect width="10" height="20"/></svg>'


@pytest.mark.parametrize(
    ('extension', 'format', 'mime_type'),
    [
        ('.png', 'PNG', 'image/png'),
        ('.jpg', 'JPEG', 'image/jpeg'),
        ('.jpeg', 'JPEG', 'image/jpeg'),
        ('.svg', 'SVG', 'image/svg+xml'),
    ],
)
def test_image_captures_supported_formats_and_serializes_original_contents(
    tmp_path, extension, format, mime_type
) -> None:
    source = tmp_path / f'render{extension}'
    if format == 'SVG':
        source.write_bytes(SVG)
    else:
        PILImage.new('RGB', (10, 20), 'red').save(source, format=format)
    contents = source.read_bytes()
    image = assets.Image(source, alt='Preview')
    source.unlink()
    store = ObjectStore(init_project_root(tmp_path / 'project'))
    serialized = serialize_asset(image, object_store=store, title='Image', description=None)
    image_object = serialized.objects[0]
    assert image_object.persisted['extension'] == extension
    assert image_object.persisted['mime_type'] == mime_type
    assert store.object_path(image_object.persisted['artifact_hash']).read_bytes() == contents
    assert serialized.definition['alt'] == 'Preview'


def test_extensionless_image_gets_format_suffix(tmp_path) -> None:
    source = tmp_path / 'download'
    source.write_bytes(SVG)
    image = assets.Image(source)
    assert image.source.extension == '.svg'
    assert image.source.path.suffix == '.svg'


@pytest.mark.parametrize('captured', [False, True])
def test_image_default_size_limit_and_explicit_override(tmp_path, captured, monkeypatch) -> None:
    monkeypatch.setattr(files.tempfile, 'tempdir', str(tmp_path))
    source = tmp_path / 'large.svg'
    contents = SVG + b' ' * (10_000_001 - len(SVG))
    source.write_bytes(contents)
    value = files.capture(source) if captured else source
    before = set(tmp_path.glob('bulletjournal-capture-*'))
    with pytest.raises(ValueError, match='max_size_bytes=10,000,000'):
        assets.Image(value)
    assert set(tmp_path.glob('bulletjournal-capture-*')) == before
    image = assets.Image(value, max_size_bytes=len(contents))
    assert image.source.size_bytes == len(contents)
    if captured:
        assert image.source is value
    assert source.read_bytes() == contents


def test_image_accepts_exact_default_size_limit(tmp_path) -> None:
    source = tmp_path / 'limit.svg'
    source.write_bytes(SVG + b' ' * (10_000_000 - len(SVG)))
    assert assets.Image(source).source.size_bytes == 10_000_000


@pytest.mark.parametrize('limit', [0, -1, True, 1.5, None])
def test_image_rejects_invalid_size_limit(tmp_path, limit) -> None:
    with pytest.raises((TypeError, ValueError), match='positive integer'):
        assets.Image(tmp_path / 'unused', max_size_bytes=limit)


@pytest.mark.parametrize(
    'contents',
    [
        b'not an image',
        b'<html/>',
        b'<svg>',
        b'GIF89a',
        b'<!DOCTYPE svg [<!ENTITY payload "test">]><svg>&payload;</svg>',
    ],
)
def test_image_rejects_invalid_content_even_with_image_extension(tmp_path, contents) -> None:
    source = tmp_path / 'invalid.png'
    source.write_bytes(contents)
    with pytest.raises(ValueError, match='PNG, JPEG, or SVG'):
        assets.Image(source)


def test_image_capture_enforces_limit_even_if_source_size_was_underreported(tmp_path, monkeypatch) -> None:
    source = tmp_path / 'growing.svg'
    source.write_bytes(SVG)
    monkeypatch.setattr(files.tempfile, 'tempdir', str(tmp_path))
    original_fstat = files.os.fstat

    def underreported_size(fd):
        metadata = original_fstat(fd)
        return SimpleNamespace(st_size=0) if stat.S_ISREG(metadata.st_mode) else metadata

    monkeypatch.setattr(files.os, 'fstat', underreported_size)
    with pytest.raises(ValueError, match='max_size_bytes=10'):
        assets.Image(source, max_size_bytes=10)
    assert list(tmp_path.glob('bulletjournal-capture-*')) == []
    assert source.read_bytes() == SVG


def test_collection_owns_images_and_remaps_multiple_image_objects(tmp_path) -> None:
    collection = assets.Collection(display_mode='2_columns')
    source = tmp_path / 'plot.svg'
    paths = []
    for index in range(2):
        source.write_bytes(SVG + b' ' * index)
        image = assets.Image(source)
        paths.append(image.source.path)
        collection.add_asset(image, name=f'image_{index}')
    collection.add_asset(assets.Markdown('Notes'), name='notes')
    source.unlink()
    del image
    gc.collect()
    assert all(path.exists() for path in paths)
    store = ObjectStore(init_project_root(tmp_path / 'project'))
    serialized = serialize_asset(collection, object_store=store, title='Gallery', description=None)
    assert [child.get('image', {}).get('object_index') for child in serialized.definition['children']] == [0, 1, None]
    assert [item.object_index for item in serialized.objects] == [0, 1]
    assert all(item.metadata['extension'] == '.svg' for item in serialized.objects)
    del collection
    gc.collect()
    assert all(not path.exists() for path in paths)
