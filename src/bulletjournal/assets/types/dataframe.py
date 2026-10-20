from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from bulletjournal.assets.base import BaseAsset
from bulletjournal.assets.prepare_utils import (
    frame_with_filters,
    frame_with_sort,
    prepared_table_payload,
    resolve_filters,
    resolve_highlights,
    resolve_page,
    resolve_sort,
)
from bulletjournal.assets.serialization import (
    SerializedAssetObject,
    SerializedAssetVersion,
    base_asset_definition,
    dataframe_column_definitions,
    dataset_modifier_schema,
)
from bulletjournal.assets.validation import validate_highlights
from bulletjournal.domain.errors import InvalidRequestError


@dataclass(slots=True)
class DataFrame(BaseAsset):
    """Create an interactive table from a ``pd.DataFrame``, with paging, sorting, and filtering available in asset
    and dashboard panels. Optional highlight rules emphasize matching cells or rows.

    - ``dataframe``: Source ``pd.DataFrame``.
    - ``highlights``: Optional list of highlight-rule dictionaries identifying a column, a match kind
      (``range``, ``value``, or ``regex``), and a six-digit hex highlight color;
      ``highlight_scope`` selects ``cell`` or ``row``.
    - ``max_text_lines``: Maximum number of lines per cell, from 1 (default) to 10.
      Values above 1 preserve line breaks and wrap text wider than the column.
    """

    dataframe: pd.DataFrame
    highlights: list[dict[str, object]] | None = None
    max_text_lines: int = 1

    asset_type_id = 'dataframe'
    interactive = True

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError('DataFrame assets require a pandas.DataFrame payload.')
        if not isinstance(self.max_text_lines, int) or isinstance(self.max_text_lines, bool):
            raise TypeError('DataFrame max_text_lines must be an integer from 1 to 10.')
        if not 1 <= self.max_text_lines <= 10:
            raise ValueError('DataFrame max_text_lines must be an integer from 1 to 10.')
        if self.highlights is not None:
            validate_highlights(self.highlights, dataframe=self.dataframe)


def serialize_dataframe(
    asset: DataFrame,
    *,
    object_store,
    title: str,
    description: str | None,
) -> SerializedAssetVersion:
    persisted = object_store.persist_value(asset.dataframe, 'pandas.DataFrame')
    column_definitions = dataframe_column_definitions(asset.dataframe)
    default_modifiers = {
        'page': {'index': 0, 'size': 25},
        'sort': [],
        'filters': [],
        'highlights': asset.highlights or [],
        'max_text_lines': asset.max_text_lines,
    }
    modifier_schema = dataset_modifier_schema(
        column_definitions,
        default_modifiers,
        filters_targets=['table'],
    )
    modifier_schema.append(dataframe_text_lines_modifier(asset.max_text_lines))
    return SerializedAssetVersion(
        asset_type=asset.asset_type_id,
        interactive=True,
        definition={
            **base_asset_definition(
                asset_type=asset.asset_type_id,
                interactive=True,
                title=title,
                description=description,
                modifier_schema=modifier_schema,
                default_modifiers=default_modifiers,
            ),
            'table_columns': [str(column) for column in asset.dataframe.columns],
            'row_count': int(asset.dataframe.shape[0]),
            'max_text_lines': asset.max_text_lines,
        },
        modifier_schema=modifier_schema,
        default_modifiers=default_modifiers,
        objects=[SerializedAssetObject(object_role='backing_dataset', persisted=persisted)],
    )


def dataframe_text_lines_modifier(default: int = 1) -> dict[str, Any]:
    return {
        'id': 'max_text_lines',
        'title': 'Max text lines',
        'kind': 'int',
        'category': 'saved_view',
        'server_targets': [],
        'default_value': default,
        'min_value': 1,
        'max_value': 10,
        'step': 1,
    }


def prepare_dataframe(
    *,
    dataset_path: Path | None,
    definition: dict[str, Any],
    default_modifiers: dict[str, Any],
    modifier_overrides: dict[str, Any],
    transient_modifiers: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    del transient_modifiers
    max_text_lines = modifier_overrides.get(
        'max_text_lines', default_modifiers.get('max_text_lines', definition.get('max_text_lines', 1))
    )
    if isinstance(max_text_lines, bool) or not isinstance(max_text_lines, int) or not 1 <= max_text_lines <= 10:
        raise InvalidRequestError('DataFrame max_text_lines must be an integer from 1 to 10.')
    table_frame, resolved_modifiers = dataframe_view(dataset_path, default_modifiers, modifier_overrides)
    schema = table_frame.collect_schema()
    column_names = list(schema.names())
    column_id_map = {str(name): name for name in column_names}
    resolved_page = resolved_modifiers['page']
    resolved_sort = resolved_modifiers['sort']
    resolved_highlights = resolve_highlights(default_modifiers, modifier_overrides, column_id_map, schema)
    return {
        'table': prepared_table_payload(
            table_frame,
            schema=schema,
            column_names=column_names,
            resolved_page=resolved_page,
            resolved_sort=resolved_sort,
            resolved_highlights=resolved_highlights,
            column_id_map=column_id_map,
        )
    }, {
        **resolved_modifiers,
        'highlights': resolved_highlights,
        'max_text_lines': max_text_lines,
    }


def dataframe_view(
    dataset_path: Path | None,
    default_modifiers: dict[str, Any],
    modifier_overrides: dict[str, Any],
) -> tuple[pl.LazyFrame, dict[str, Any]]:
    frame = pl.LazyFrame() if dataset_path is None else pl.scan_parquet(dataset_path)
    schema = frame.collect_schema()
    column_id_map = {str(name): name for name in schema.names()}
    resolved_page = resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
    frame = frame_with_filters(frame, resolved_filters, column_id_map)
    frame = frame_with_sort(frame, resolved_sort, column_id_map)
    return frame, {'page': resolved_page, 'sort': resolved_sort, 'filters': resolved_filters}
