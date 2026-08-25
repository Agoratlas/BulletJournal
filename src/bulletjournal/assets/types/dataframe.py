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


@dataclass(slots=True)
class DataFrame(BaseAsset):
    dataframe: pd.DataFrame
    highlights: list[dict[str, object]] | None = None

    asset_type_id = 'dataframe'
    interactive = True

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError('DataFrame assets require a pandas.DataFrame payload.')
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
    }
    modifier_schema = dataset_modifier_schema(
        column_definitions,
        default_modifiers,
        filters_targets=['table'],
    )
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
        },
        modifier_schema=modifier_schema,
        default_modifiers=default_modifiers,
        objects=[SerializedAssetObject(object_role='backing_dataset', persisted=persisted)],
    )


def prepare_dataframe(
    *,
    dataset_path: Path,
    definition: dict[str, Any],
    default_modifiers: dict[str, Any],
    modifier_overrides: dict[str, Any],
    transient_modifiers: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    del definition, transient_modifiers
    frame = pl.scan_parquet(dataset_path)
    schema = frame.collect_schema()
    column_names = list(schema.names())
    column_id_map = {str(name): name for name in column_names}
    resolved_page = resolve_page(default_modifiers, modifier_overrides)
    resolved_sort = resolve_sort(default_modifiers, modifier_overrides, column_id_map)
    resolved_filters = resolve_filters(default_modifiers, modifier_overrides, column_id_map, schema)
    resolved_highlights = resolve_highlights(default_modifiers, modifier_overrides, column_id_map, schema)
    table_frame = frame_with_filters(frame, resolved_filters, column_id_map)
    table_frame = frame_with_sort(table_frame, resolved_sort, column_id_map)
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
        'page': resolved_page,
        'sort': resolved_sort,
        'filters': resolved_filters,
        'highlights': resolved_highlights,
    }
