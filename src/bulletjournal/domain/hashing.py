from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode('utf-8'))


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def hash_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def combine_hashes(parts: list[str]) -> str:
    payload = '||'.join(sorted(parts))
    return sha256_text(payload)
