from __future__ import annotations

import gzip
import io
import json
import mimetypes
import pickle
from pathlib import Path
from typing import Any

import pandas as pd

from bulletjournal.config import IMAGE_PREVIEW_MAX_BYTES, MAX_PREVIEW_COLS, MAX_PREVIEW_ROWS, MAX_SIMPLE_PREVIEW_CHARS
from bulletjournal.domain.enums import StorageKind


def serialize_value(value: Any, data_type: str) -> dict[str, Any]:
    if data_type in {'int', 'float', 'bool', 'str', 'list', 'dict'}:
        payload = json.dumps(value, ensure_ascii=True, sort_keys=True).encode('utf-8')
        return {
            'bytes': payload,
            'storage_kind': StorageKind.JSON.value,
            'data_type': data_type,
            'extension': '.json',
            'mime_type': 'application/json',
            'preview': _simple_preview(value),
        }
    if data_type == 'pandas.DataFrame':
        buffer = io.BytesIO()
        value.to_parquet(buffer, index=False)
        return {
            'bytes': buffer.getvalue(),
            'storage_kind': StorageKind.PARQUET.value,
            'data_type': data_type,
            'preview': _dataframe_preview(value),
            'extension': '.parquet',
        }
    if data_type == 'pandas.Series':
        buffer = io.BytesIO()
        value.to_frame(name=value.name or 'value').to_parquet(buffer, index=False)
        return {
            'bytes': buffer.getvalue(),
            'storage_kind': StorageKind.PARQUET.value,
            'data_type': data_type,
            'preview': _series_preview(value),
            'extension': '.parquet',
        }
    payload = gzip.compress(pickle.dumps(value))
    return {
        'bytes': payload,
        'storage_kind': StorageKind.PICKLE.value,
        'data_type': data_type,
        'preview': {'kind': 'object', 'repr': repr(value)[:MAX_SIMPLE_PREVIEW_CHARS]},
        'extension': '.pkl.gz',
    }


def deserialize_value(payload: bytes, data_type: str) -> Any:
    if data_type in {'int', 'float', 'bool', 'str', 'list', 'dict'}:
        return json.loads(payload.decode('utf-8'))
    if data_type == 'pandas.DataFrame':
        return pd.read_parquet(io.BytesIO(payload))
    if data_type == 'pandas.Series':
        frame = pd.read_parquet(io.BytesIO(payload))
        return frame.iloc[:, 0]
    return pickle.loads(gzip.decompress(payload))  # noqa: S301


def serialize_file(path: Path, *, extension: str | None = None) -> dict[str, Any]:
    raw_bytes = path.read_bytes()
    suffix = extension or path.suffix or None
    mime_type, _ = mimetypes.guess_type(path.name)
    preview = {
        'kind': 'file',
        'filename': path.name,
        'size_bytes': len(raw_bytes),
        'extension': suffix,
        'mime_type': mime_type,
    }
    if mime_type and mime_type.startswith('image/') and len(raw_bytes) <= IMAGE_PREVIEW_MAX_BYTES:
        preview['image_inline'] = True
    return {
        'bytes': raw_bytes,
        'storage_kind': StorageKind.FILE.value,
        'data_type': 'file',
        'extension': suffix,
        'mime_type': mime_type,
        'preview': preview,
    }


def _simple_preview(value: Any) -> dict[str, Any]:
    representation = repr(value)
    cropped = representation[:MAX_SIMPLE_PREVIEW_CHARS]
    return {'kind': 'simple', 'repr': cropped, 'truncated': len(representation) > len(cropped)}


def _dataframe_preview(frame: pd.DataFrame) -> dict[str, Any]:
    sample = frame.iloc[:MAX_PREVIEW_ROWS, :MAX_PREVIEW_COLS]
    return {
        'kind': 'dataframe',
        'rows': int(frame.shape[0]),
        'columns': int(frame.shape[1]),
        'column_names': list(map(str, frame.columns[:MAX_PREVIEW_COLS])),
        'sample': sample.astype(object).where(sample.notna(), None).to_dict(orient='records'),
    }


def _series_preview(series: pd.Series) -> dict[str, Any]:
    sample = series.iloc[:MAX_PREVIEW_ROWS].tolist()
    return {'kind': 'series', 'rows': int(series.shape[0]), 'sample': sample}
