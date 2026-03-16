from __future__ import annotations

from pathlib import Path

from bulletjournal.domain.hashing import sha256_text


def compute_source_hash(path: Path) -> str:
    return normalized_source_hash_text(path.read_text(encoding='utf-8'))


def normalized_source_hash_text(source: str) -> str:
    normalized = source.replace('\r\n', '\n').replace('\r', '\n').rstrip()
    return sha256_text(normalized)
