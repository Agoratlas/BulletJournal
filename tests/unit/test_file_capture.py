import gc
from pathlib import Path

import pytest

from bulletjournal.domain.enums import ArtifactRole, LineageMode, ValidationSeverity
from bulletjournal.domain.models import AssetDeclaration, Port
from bulletjournal.parser.interface_parser import parse_notebook_contract
from bulletjournal.runtime import artifacts, assets, files
from bulletjournal.runtime.context import _RUNTIME_CONTEXT, Binding, RuntimeContext
from bulletjournal.storage.project_fs import init_project_root


def test_capture_owns_contents_and_cleans_up_after_last_owner(tmp_path) -> None:
    source = tmp_path / 'render.svg'
    source.write_bytes(b'<svg/>')
    snapshot = files.capture(source)
    captured_path = snapshot.path
    assert snapshot.extension == '.svg'
    assert captured_path.suffix == '.svg'
    assert snapshot.size_bytes == 6
    source.write_bytes(b'changed')
    source.unlink()
    assert captured_path.read_bytes() == b'<svg/>'
    assert captured_path.stat().st_mode & 0o222 == 0
    owner = files.capture(snapshot)
    assert owner is snapshot
    del snapshot
    gc.collect()
    assert captured_path.exists()
    del owner
    gc.collect()
    assert not captured_path.parent.exists()


@pytest.mark.parametrize('extension', ['.tar.gz', 'tar.gz'])
def test_capture_preserves_explicit_compound_extension(tmp_path, extension) -> None:
    source = tmp_path / 'download'
    source.write_bytes(b'archive')
    snapshot = files.capture(source, extension=extension)
    assert snapshot.extension == '.tar.gz'
    assert snapshot.path.name.endswith('.tar.gz')
    assert snapshot.path.read_bytes() == b'archive'


def test_capture_failure_removes_temporary_copy(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(files.tempfile, 'tempdir', str(tmp_path))
    with pytest.raises(FileNotFoundError):
        files.capture(tmp_path / 'missing')
    assert list(tmp_path.glob('bulletjournal-capture-*')) == []


@pytest.mark.parametrize('extension', ['../png', '.png/name', '.'])
def test_capture_rejects_invalid_extensions(tmp_path, extension) -> None:
    with pytest.raises(ValueError, match='extension'):
        files.capture(tmp_path / 'unused', extension=extension)


@pytest.mark.parametrize('direct_path', [False, True])
def test_file_publication_and_image_share_contents_and_preserve_pull_extension(tmp_path, direct_path) -> None:
    root = init_project_root(tmp_path / 'project').root
    producer = RuntimeContext(
        project_root=root,
        node_id='producer',
        run_id='capture-run',
        source_hash='source',
        lineage_mode=LineageMode.MANAGED,
        bindings={},
        outputs={'render': Port(name='render', data_type='file', role=ArtifactRole.OUTPUT, kind='file')},
        asset_declarations={
            'preview': AssetDeclaration(
                node_id='producer', name='preview', title='Preview', description=None, declared_asset_type='image'
            )
        },
    )
    source = tmp_path / 'render.svg'
    contents = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
    source.write_bytes(contents)
    snapshot = files.capture(source)
    image = assets.Image(snapshot)
    token = _RUNTIME_CONTEXT.set(producer)
    try:
        artifacts.push_file(source if direct_path else snapshot, name='render')
        source.unlink()
        assets.push(image, name='preview', title='Preview', asset_type=assets.Image)
    finally:
        _RUNTIME_CONTEXT.reset(token)
    artifact = producer.db.get_artifact_head('producer', 'render')
    asset = producer.db.get_asset_head('producer', 'preview')
    assert artifact['extension'] == '.svg'
    assert asset['objects'][0]['artifact_hash'] == artifact['artifact_hash']
    assert asset['objects'][0]['metadata']['extension'] == '.svg'
    consumer = RuntimeContext(
        project_root=root,
        node_id='consumer',
        run_id='pull-run',
        source_hash='consumer',
        lineage_mode=LineageMode.MANAGED,
        bindings={'render': Binding(source_node='producer', source_artifact='render', data_type='file')},
        outputs={},
    )
    token = _RUNTIME_CONTEXT.set(consumer)
    try:
        pulled = Path(artifacts.pull_file(name='render'))
    finally:
        _RUNTIME_CONTEXT.reset(token)
    assert pulled.suffix == '.svg'
    assert pulled.read_bytes() == contents


def test_parser_accepts_capture_inside_context_and_declares_only_publications(tmp_path) -> None:
    notebook = tmp_path / 'capture.py'
    notebook.write_text("""import marimo
app = marimo.App()
with app.setup:
    from bulletjournal.runtime import artifacts, assets, files
    import tempfile
    from pathlib import Path
@app.cell
def _():
    gallery = assets.Collection()
    with tempfile.TemporaryDirectory() as _tmp:
        _path = Path(_tmp) / 'plot.svg'
        _path.write_text('<svg/>')
        snapshot = files.capture(_path)
        for _index in range(2):
            gallery.add_asset(assets.Image(snapshot), name=str(_index))
    artifacts.push_file(snapshot, name='plot')
    artifacts.push_file(source=snapshot, name='copy')
    assets.push(gallery, name='gallery', title='Gallery', asset_type=assets.Collection)
    assets.push(assets.Image(snapshot), name='image', title='Image', asset_type=assets.Image)
    return
""")
    contract = parse_notebook_contract(notebook, 'capture')
    assert not [issue for issue in contract.issues if issue.severity == ValidationSeverity.ERROR]
    assert [(port.name, port.data_type, port.kind) for port in contract.interface.outputs] == [
        ('plot', 'file', 'file'),
        ('copy', 'file', 'file'),
    ]
    assert [asset.declared_asset_type for asset in contract.asset_declarations] == ['collection', 'image']


def test_parser_still_rejects_nested_file_publication(tmp_path) -> None:
    notebook = tmp_path / 'nested.py'
    notebook.write_text("""import marimo
app = marimo.App()
with app.setup:
    from bulletjournal.runtime import artifacts
@app.cell
def _():
    if True:
        artifacts.push_file('plot.png', name='plot')
    return
""")
    contract = parse_notebook_contract(notebook, 'nested')
    assert 'nested_runtime_call' in {issue.code for issue in contract.issues}
