from __future__ import annotations

from typing import Any

from bulletjournal.assets.runtime_types import BaseAsset, DataFrameAsset, MarkdownAsset
from bulletjournal.runtime.context import current_runtime_context


class Markdown(MarkdownAsset):
    pass


class DataFrame(DataFrameAsset):
    pass


def push(
    asset: BaseAsset,
    *,
    name: str,
    title: str,
    description: str | None = None,
    asset_type: type[BaseAsset] | None = None,
    **kwargs: Any,
) -> None:
    if kwargs:
        unexpected = ', '.join(sorted(kwargs))
        raise TypeError(f'Unexpected asset push kwargs: {unexpected}')
    context = current_runtime_context()
    context.finalize_asset_push(
        asset=asset,
        name=name,
        title=title,
        description=description,
        asset_type=asset_type,
    )


__all__ = ['BaseAsset', 'DataFrame', 'Markdown', 'push']
