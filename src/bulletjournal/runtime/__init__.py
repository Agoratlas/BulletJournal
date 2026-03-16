from __future__ import annotations

from typing import TYPE_CHECKING, Any


class _ArtifactsProxy:
    def __getattr__(self, name: str) -> Any:
        from bulletjournal.runtime.artifacts import artifacts as runtime_artifacts

        return getattr(runtime_artifacts, name)

    def __repr__(self) -> str:
        return '<BulletJournal artifacts proxy>'


artifacts = _ArtifactsProxy()

if TYPE_CHECKING:
    from bulletjournal.runtime.artifacts import _ArtifactsAPI
    from typing import cast

    artifacts = cast(_ArtifactsAPI, artifacts)


__all__ = ['artifacts']
