from __future__ import annotations

import gzip
import io
import json
import mimetypes
import pickle
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from bulletjournal.config import IMAGE_PREVIEW_MAX_BYTES, MAX_PREVIEW_COLS, MAX_PREVIEW_ROWS, MAX_SIMPLE_PREVIEW_CHARS
from bulletjournal.domain.enums import StorageKind
from bulletjournal.domain.type_system import CANONICAL_TYPES


def serialize_value(value: Any, data_type: str) -> dict[str, Any]:
    if value is None:
        payload = b'null'
        return {
            'bytes': payload,
            'storage_kind': StorageKind.JSON.value,
            'data_type': data_type,
            'extension': '.json',
            'mime_type': 'application/json',
            'preview': {'kind': 'empty'},
        }
    validate_runtime_value_type(value, data_type, operation='export')
    if data_type in {'int', 'float', 'bool', 'str', 'list', 'dict'}:
        payload = json.dumps(value, ensure_ascii=True, sort_keys=True).encode('utf-8')
        return {
            'bytes': payload,
            'storage_kind': StorageKind.JSON.value,
            'data_type': data_type,
            'extension': '.json',
            'mime_type': 'application/json',
            'preview': {
                **_simple_preview(value),
                **_json_preview_metadata(value),
            },
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
        series_json = json.dumps(_json_safe_preview_value(value.tolist()), ensure_ascii=True, indent=2)
        return {
            'bytes': buffer.getvalue(),
            'storage_kind': StorageKind.PARQUET.value,
            'data_type': data_type,
            'preview': {
                **_series_preview(value),
                **_preview_text_metadata(_series_inspector_text(value)),
                **({'editor_text': series_json} if len(series_json.encode('utf-8')) <= 10_000 else {}),
            },
            'extension': '.parquet',
        }
    payload = gzip.compress(pickle.dumps(value))
    return {
        'bytes': payload,
        'storage_kind': StorageKind.PICKLE.value,
        'data_type': data_type,
        'preview': {
            'kind': 'object',
            'repr': repr(value)[:MAX_SIMPLE_PREVIEW_CHARS],
            **_preview_text_metadata(repr(value)),
        },
        'extension': '.pkl.gz',
    }


def deserialize_value(payload: bytes, data_type: str) -> Any:
    value: Any
    if data_type in {'int', 'float', 'bool', 'str', 'list', 'dict'}:
        value = json.loads(payload.decode('utf-8'))
    elif data_type == 'pandas.DataFrame':
        value = pd.read_parquet(io.BytesIO(payload))
    elif data_type == 'pandas.Series':
        frame = pd.read_parquet(io.BytesIO(payload))
        value = frame.iloc[:, 0]
    else:
        value = pickle.loads(gzip.decompress(payload))  # noqa: S301
    if value is None:
        return None
    validate_runtime_value_type(value, data_type, operation='import')
    return value


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
        **_preview_text_metadata(_file_inspector_text(path=path, mime_type=mime_type, size_bytes=len(raw_bytes))),
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
        'sample': _json_safe_preview_value(sample.astype(object).where(sample.notna(), None).to_dict(orient='records')),
        **_preview_text_metadata(_dataframe_inspector_text(frame)),
    }


def _series_preview(series: pd.Series) -> dict[str, Any]:
    sample = series.iloc[:MAX_PREVIEW_ROWS].tolist()
    return {'kind': 'series', 'rows': int(series.shape[0]), 'sample': _json_safe_preview_value(sample)}


def _json_preview_metadata(value: Any) -> dict[str, Any]:
    inspector_text = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
    metadata = _preview_text_metadata(inspector_text)
    if len(inspector_text.encode('utf-8')) <= 10_000:
        metadata['editor_text'] = inspector_text
    return metadata


def _preview_text_metadata(text: str) -> dict[str, Any]:
    payload = text.encode('utf-8', errors='replace')
    if len(payload) <= 10_000:
        return {'inspector_text': text, 'inspector_truncated': False}
    truncated = payload[:10_000].decode('utf-8', errors='ignore')
    return {'inspector_text': truncated, 'inspector_truncated': True}


def _dataframe_inspector_text(frame: pd.DataFrame) -> str:
    return frame.to_string(max_rows=MAX_PREVIEW_ROWS * 4, max_cols=MAX_PREVIEW_COLS * 2)


def _series_inspector_text(series: pd.Series) -> str:
    return series.to_string(max_rows=MAX_PREVIEW_ROWS * 6)


def _file_inspector_text(*, path: Path, mime_type: str | None, size_bytes: int) -> str:
    details = {
        'filename': path.name,
        'mime_type': mime_type,
        'extension': path.suffix or None,
        'size_bytes': size_bytes,
    }
    return json.dumps(details, ensure_ascii=True, indent=2, sort_keys=True)


def _json_safe_preview_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, pd.Timestamp | datetime | date | time):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe_preview_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe_preview_value(item) for item in value]
    item = getattr(value, 'item', None)
    if callable(item):
        normalized = item()
        if normalized is not value:
            return _json_safe_preview_value(normalized)
    return repr(value)


def validate_runtime_value_type(value: Any, data_type: Any, *, operation: str) -> None:
    if _value_matches_declared_type(value, data_type):
        return
    raise TypeError(
        f'Artifact {operation} type mismatch: '
        f'expected {_declared_type_name(data_type)}, '
        f'got {_runtime_type_name(value)}.'
    )


def _value_matches_declared_type(value: Any, data_type: Any) -> bool:
    if isinstance(data_type, str):
        if data_type in CANONICAL_TYPES:
            return _value_matches_canonical_data_type(value, data_type)
        runtime_name = _runtime_type_name(value)
        return runtime_name == data_type or type(value).__name__ == data_type
    if data_type in {int, float, bool, str, list, dict, object}:
        return _value_matches_canonical_data_type(value, data_type.__name__)
    if isinstance(data_type, type):
        return isinstance(value, data_type)
    declared = _declared_type_name(data_type)
    if declared == 'object':
        return True
    runtime_name = _runtime_type_name(value)
    return runtime_name == declared or type(value).__name__ == declared


def _value_matches_canonical_data_type(value: Any, data_type: str) -> bool:
    if data_type == 'int':
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type == 'float':
        return isinstance(value, float) and not isinstance(value, bool)
    if data_type == 'bool':
        return isinstance(value, bool)
    if data_type == 'str':
        return isinstance(value, str)
    if data_type == 'list':
        return isinstance(value, list)
    if data_type == 'dict':
        return isinstance(value, dict)
    if data_type == 'pandas.DataFrame':
        return isinstance(value, pd.DataFrame)
    if data_type == 'pandas.Series':
        return isinstance(value, pd.Series)
    if data_type == 'object':
        return True
    if data_type == 'networkx.Graph':
        return _matches_networkx_type(value, 'Graph')
    if data_type == 'networkx.DiGraph':
        return _matches_networkx_type(value, 'DiGraph')
    return True


def _declared_type_name(data_type: Any) -> str:
    if isinstance(data_type, str):
        return data_type
    if data_type in {int, float, bool, str, list, dict, object}:
        return data_type.__name__
    module = getattr(data_type, '__module__', '')
    name = getattr(data_type, '__name__', None)
    if isinstance(name, str) and name:
        if module in {'', 'builtins'}:
            return name
        return f'{module}.{name}'
    return 'object'


def _matches_networkx_type(value: Any, expected_name: str) -> bool:
    value_type = type(value)
    module = getattr(value_type, '__module__', '')
    name = getattr(value_type, '__name__', '')
    return module.startswith('networkx') and name == expected_name


def _runtime_type_name(value: Any) -> str:
    value_type = type(value)
    module = getattr(value_type, '__module__', '')
    name = getattr(value_type, '__name__', value_type.__class__.__name__)
    if module in {'', 'builtins'}:
        return str(name)
    return f'{module}.{name}'
