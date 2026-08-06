from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

from bulletjournal.storage.object_store import ObjectStore
from bulletjournal.storage.project_fs import ProjectPaths
from bulletjournal.storage.project_lock import ProjectLock
from bulletjournal.storage.state_db import StateDB


@dataclass(frozen=True, slots=True)
class ObjectGCSettings:
    enabled: bool = True
    object_retention_seconds: int = 3600
    version_retention_seconds: int = 3600
    min_interval_seconds: int = 900
    temp_retention_seconds: int = 86400
    batch_size: int = 250
    max_batch_bytes: int = 64 * 1024 * 1024
    time_budget_seconds: float = 2.0

    def __post_init__(self) -> None:
        integer_values = {
            'object_retention_seconds': self.object_retention_seconds,
            'version_retention_seconds': self.version_retention_seconds,
            'min_interval_seconds': self.min_interval_seconds,
            'temp_retention_seconds': self.temp_retention_seconds,
            'batch_size': self.batch_size,
            'max_batch_bytes': self.max_batch_bytes,
        }
        if any(type(value) is not int or value < 0 for value in integer_values.values()):
            raise ValueError('GC integer settings must be non-negative integers.')
        if self.batch_size == 0 or self.max_batch_bytes == 0:
            raise ValueError('GC batch_size and max_batch_bytes must be greater than zero.')
        if not isinstance(self.time_budget_seconds, int | float) or self.time_budget_seconds <= 0:
            raise ValueError('GC time_budget_seconds must be greater than zero.')

    @classmethod
    def from_project_meta(cls, db: StateDB) -> ObjectGCSettings:
        values = db.list_project_meta()

        def integer(key: str, default: int) -> int:
            raw = values.get(key)
            if raw is None:
                return default
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f'Invalid {key}: expected an integer.') from exc
            return value

        def boolean(key: str, default: bool) -> bool:
            raw = values.get(key)
            if raw is None:
                return default
            if raw.lower() in {'true', '1'}:
                return True
            if raw.lower() in {'false', '0'}:
                return False
            raise ValueError(f'Invalid {key}: expected true or false.')

        defaults = cls()
        return cls(
            enabled=boolean('gc_enabled', defaults.enabled),
            object_retention_seconds=integer('gc_object_retention_seconds', defaults.object_retention_seconds),
            version_retention_seconds=integer('gc_version_retention_seconds', defaults.version_retention_seconds),
            min_interval_seconds=integer('gc_min_interval_seconds', defaults.min_interval_seconds),
            temp_retention_seconds=integer('gc_temp_retention_seconds', defaults.temp_retention_seconds),
            batch_size=integer('gc_batch_size', defaults.batch_size),
            max_batch_bytes=integer('gc_max_batch_bytes', defaults.max_batch_bytes),
        )


@dataclass(slots=True)
class ObjectGCReport:
    dry_run: bool
    objects_examined: int = 0
    artifact_roots: int = 0
    asset_roots: int = 0
    pin_roots: int = 0
    lease_roots: int = 0
    tombstones_expired: int = 0
    artifact_versions_pruned: int = 0
    asset_versions_pruned: int = 0
    temp_files_deleted: int = 0
    objects_eligible: int = 0
    objects_deleted: int = 0
    bytes_reclaimed: int = 0
    failures: int = 0
    remaining_work: bool = False
    deferred_reason: str | None = None
    duration_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ObjectGarbageCollector:
    def __init__(
        self,
        paths: ProjectPaths,
        settings: ObjectGCSettings | None = None,
        *,
        activity_check: Callable[[], str | None] | None = None,
    ):
        self.paths = paths
        self.db = StateDB(paths.state_db_path)
        self.store = ObjectStore(paths)
        self.lock = ProjectLock(paths.project_lock_path)
        self.settings = settings or ObjectGCSettings.from_project_meta(self.db)
        self.activity_check = activity_check

    def collect(
        self,
        *,
        dry_run: bool = True,
        now: datetime | None = None,
        lock_timeout: float = 0.1,
    ) -> ObjectGCReport:
        report = ObjectGCReport(dry_run=dry_run)
        started = monotonic()
        if not self.settings.enabled:
            report.deferred_reason = 'disabled'
            return report
        if self.activity_check is not None:
            report.deferred_reason = self.activity_check()
            if report.deferred_reason:
                return report
        current = (now or datetime.now(tz=UTC)).astimezone(UTC)
        try:
            with self.lock.exclusive(timeout=lock_timeout):
                self._reconcile(report, dry_run=dry_run)
                self._expire_tombstones(current, report, dry_run=dry_run)
                self._prune_versions(current, report, dry_run=dry_run)
                roots = self._roots(current, report)
                self._refresh_unreferenced_at(current, roots, dry_run=dry_run)
                candidates = self._candidates(current, roots, report, started)
                if not dry_run:
                    self._delete(candidates, current, report)
                    self._cleanup_temp(current, report)
                    self.db.set_project_meta('gc_last_completed_at', _iso(current))
        except TimeoutError:
            report.deferred_reason = 'project_lock_busy'
        finally:
            report.duration_seconds = monotonic() - started
        return report

    def recover(self, *, dry_run: bool = False, lock_timeout: float = 1.0) -> ObjectGCReport:
        report = ObjectGCReport(dry_run=dry_run)
        started = monotonic()
        try:
            with self.lock.exclusive(timeout=lock_timeout):
                self._reconcile(report, dry_run=dry_run)
        except TimeoutError:
            report.deferred_reason = 'project_lock_busy'
        report.duration_seconds = monotonic() - started
        return report

    def _roots(self, now: datetime, report: ObjectGCReport) -> set[str]:
        now_iso = _iso(now)
        with self.db._connection() as connection:
            expired_hashes = {
                str(row[0])
                for row in connection.execute(
                    'SELECT artifact_hash FROM object_leases WHERE expires_at <= ? '
                    'UNION SELECT artifact_hash FROM object_pins WHERE expires_at IS NOT NULL AND expires_at <= ? '
                    "AND owner_kind != 'tombstone'",
                    (now_iso, now_iso),
                )
            }
            connection.execute('DELETE FROM object_leases WHERE expires_at <= ?', (now_iso,))
            connection.execute(
                'DELETE FROM object_pins WHERE expires_at IS NOT NULL AND expires_at <= ? '
                "AND owner_kind != 'tombstone'",
                (now_iso,),
            )
            for artifact_hash in expired_hashes:
                connection.execute(
                    'UPDATE objects SET unreferenced_at = ? WHERE artifact_hash = ? '
                    'AND NOT EXISTS (SELECT 1 FROM artifact_versions WHERE artifact_hash = ?) '
                    'AND NOT EXISTS (SELECT 1 FROM asset_version_objects WHERE artifact_hash = ?) '
                    'AND NOT EXISTS (SELECT 1 FROM object_pins WHERE artifact_hash = ?) '
                    'AND NOT EXISTS (SELECT 1 FROM object_leases WHERE artifact_hash = ?)',
                    (now_iso, artifact_hash, artifact_hash, artifact_hash, artifact_hash, artifact_hash),
                )
            artifact = {
                str(row[0])
                for row in connection.execute(
                    'SELECT DISTINCT av.artifact_hash FROM artifact_heads ah '
                    'JOIN artifact_versions av ON av.version_id = ah.current_version_id '
                    'WHERE ah.incarnation_id IS NULL OR ah.incarnation_id IN '
                    "(SELECT incarnation_id FROM node_incarnations WHERE status = 'live')"
                )
            }
            assets = {
                str(row[0])
                for row in connection.execute(
                    'SELECT DISTINCT avo.artifact_hash FROM asset_heads ah '
                    'JOIN asset_version_objects avo ON avo.asset_version_id = ah.current_asset_version_id '
                    'WHERE ah.incarnation_id IS NULL OR ah.incarnation_id IN '
                    "(SELECT incarnation_id FROM node_incarnations WHERE status = 'live')"
                )
            }
            open_publications = {
                str(row[0])
                for row in connection.execute(
                    'SELECT DISTINCT av.artifact_hash FROM artifact_versions av '
                    'JOIN publication_batches pb ON pb.publication_id = av.publication_id '
                    "WHERE pb.state = 'open' UNION SELECT DISTINCT avo.artifact_hash FROM asset_version_objects avo "
                    'JOIN asset_versions av ON av.asset_version_id = avo.asset_version_id '
                    'JOIN publication_batches pb ON pb.publication_id = av.publication_id '
                    "WHERE pb.state = 'open'"
                )
            }
            pins = {
                str(row[0])
                for row in connection.execute(
                    'SELECT DISTINCT artifact_hash FROM object_pins WHERE expires_at IS NULL OR expires_at > ?',
                    (now_iso,),
                )
            }
            leases = {
                str(row[0])
                for row in connection.execute(
                    'SELECT DISTINCT artifact_hash FROM object_leases WHERE expires_at > ?', (now_iso,)
                )
            }
            connection.commit()
        report.artifact_roots = len(artifact)
        report.asset_roots = len(assets)
        report.pin_roots = len(pins)
        report.lease_roots = len(leases)
        return artifact | assets | open_publications | pins | leases

    def _expire_tombstones(self, now: datetime, report: ObjectGCReport, *, dry_run: bool) -> None:
        now_iso = _iso(now)
        with self.db._connection() as connection:
            rows = connection.execute(
                "SELECT tombstone_id FROM node_tombstones WHERE status = 'retained' AND expires_at <= ? "
                'ORDER BY expires_at, tombstone_id LIMIT ?',
                (now_iso, self.settings.batch_size),
            ).fetchall()
        report.tombstones_expired = len(rows)
        if dry_run:
            return
        for row in rows:
            self.db.expire_tombstone(str(row['tombstone_id']), expired_at=now_iso)

    def _prune_versions(self, now: datetime, report: ObjectGCReport, *, dry_run: bool) -> None:
        cutoff = _iso(now - timedelta(seconds=self.settings.version_retention_seconds))
        with self.db._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            artifact_ids = [
                int(row[0])
                for row in connection.execute(
                    'SELECT av.version_id FROM artifact_versions av '
                    'WHERE av.created_at <= ? '
                    'AND NOT EXISTS (SELECT 1 FROM artifact_heads ah WHERE ah.current_version_id = av.version_id) '
                    'AND NOT EXISTS (SELECT 1 FROM publication_batches pb '
                    "WHERE pb.publication_id = av.publication_id AND pb.state = 'open') "
                    "AND NOT EXISTS (SELECT 1 FROM object_pins op WHERE op.owner_kind = 'tombstone' "
                    'AND op.artifact_hash = av.artifact_hash AND (op.expires_at IS NULL OR op.expires_at > ?)) '
                    'ORDER BY av.version_id LIMIT ?',
                    (cutoff, _iso(now), self.settings.batch_size),
                ).fetchall()
            ]
            asset_ids = [
                int(row[0])
                for row in connection.execute(
                    'SELECT av.asset_version_id FROM asset_versions av '
                    'WHERE av.created_at <= ? '
                    'AND NOT EXISTS (SELECT 1 FROM asset_heads ah '
                    'WHERE ah.current_asset_version_id = av.asset_version_id) '
                    'AND NOT EXISTS (SELECT 1 FROM publication_batches pb '
                    "WHERE pb.publication_id = av.publication_id AND pb.state = 'open') "
                    'AND NOT EXISTS (SELECT 1 FROM asset_version_objects avo JOIN object_pins op '
                    "ON op.artifact_hash = avo.artifact_hash AND op.owner_kind = 'tombstone' "
                    'AND (op.expires_at IS NULL OR op.expires_at > ?) '
                    'WHERE avo.asset_version_id = av.asset_version_id) '
                    'ORDER BY av.asset_version_id LIMIT ?',
                    (cutoff, _iso(now), self.settings.batch_size),
                ).fetchall()
            ]
            report.artifact_versions_pruned = len(artifact_ids)
            report.asset_versions_pruned = len(asset_ids)
            if dry_run:
                connection.rollback()
                return
            if artifact_ids:
                placeholders = ','.join('?' for _ in artifact_ids)
                connection.execute(
                    f'UPDATE run_outputs SET version_id = NULL WHERE version_id IN ({placeholders})',  # noqa: S608
                    artifact_ids,
                )
                connection.execute(
                    f'DELETE FROM artifact_versions WHERE version_id IN ({placeholders})',  # noqa: S608
                    artifact_ids,
                )
            if asset_ids:
                placeholders = ','.join('?' for _ in asset_ids)
                connection.execute(
                    f'DELETE FROM asset_version_objects WHERE asset_version_id IN ({placeholders})',  # noqa: S608
                    asset_ids,
                )
                connection.execute(
                    f'DELETE FROM asset_versions WHERE asset_version_id IN ({placeholders})',  # noqa: S608
                    asset_ids,
                )
            violations = connection.execute('PRAGMA foreign_key_check').fetchall()
            dangling = connection.execute(
                'SELECT 1 FROM artifact_heads ah LEFT JOIN artifact_versions av '
                'ON av.version_id = ah.current_version_id '
                'WHERE ah.current_version_id IS NOT NULL AND av.version_id IS NULL '
                'UNION ALL SELECT 1 FROM asset_heads ah LEFT JOIN asset_versions av '
                'ON av.asset_version_id = ah.current_asset_version_id '
                'WHERE ah.current_asset_version_id IS NOT NULL AND av.asset_version_id IS NULL LIMIT 1'
            ).fetchone()
            if violations or dangling:
                raise sqlite3.IntegrityError('Version pruning would violate a foreign key or head invariant.')
            connection.commit()

    def _candidates(
        self, now: datetime, roots: set[str], report: ObjectGCReport, started: float
    ) -> list[tuple[str, int]]:
        cutoff = _iso(now - timedelta(seconds=self.settings.object_retention_seconds))
        selected: list[tuple[str, int]] = []
        total_bytes = 0
        cursor = ''
        while len(selected) < self.settings.batch_size:
            with self.db._connection() as connection:
                rows = connection.execute(
                    "SELECT artifact_hash, size_bytes FROM objects WHERE gc_state = 'active' "
                    'AND unreferenced_at IS NOT NULL AND unreferenced_at <= ? AND artifact_hash > ? '
                    'AND NOT EXISTS (SELECT 1 FROM artifact_versions av '
                    'WHERE av.artifact_hash = objects.artifact_hash) '
                    'AND NOT EXISTS (SELECT 1 FROM asset_version_objects avo '
                    'WHERE avo.artifact_hash = objects.artifact_hash) '
                    'ORDER BY artifact_hash LIMIT ?',
                    (cutoff, cursor, self.settings.batch_size + 1),
                ).fetchall()
            if not rows:
                break
            for row in rows:
                artifact_hash, size_bytes = str(row['artifact_hash']), int(row['size_bytes'])
                cursor = artifact_hash
                report.objects_examined += 1
                if artifact_hash in roots:
                    continue
                if selected and total_bytes + size_bytes > self.settings.max_batch_bytes:
                    report.remaining_work = True
                    break
                selected.append((artifact_hash, size_bytes))
                total_bytes += size_bytes
                if (
                    len(selected) >= self.settings.batch_size
                    or monotonic() - started >= self.settings.time_budget_seconds
                ):
                    report.remaining_work = True
                    break
            if report.remaining_work or len(rows) <= self.settings.batch_size:
                break
        report.objects_eligible = len(selected)
        return selected

    def _refresh_unreferenced_at(self, now: datetime, roots: set[str], *, dry_run: bool) -> None:
        if dry_run:
            return
        now_iso = _iso(now)
        with self.db._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if roots:
                placeholders = ','.join('?' for _ in roots)
                connection.execute(
                    f'UPDATE objects SET unreferenced_at = NULL WHERE artifact_hash IN ({placeholders})',  # noqa: S608
                    tuple(roots),
                )
            connection.execute(
                "UPDATE objects SET unreferenced_at = ? WHERE gc_state = 'active' AND unreferenced_at IS NULL "
                'AND NOT EXISTS (SELECT 1 FROM artifact_versions av WHERE av.artifact_hash = objects.artifact_hash) '
                'AND NOT EXISTS (SELECT 1 FROM asset_version_objects avo '
                'WHERE avo.artifact_hash = objects.artifact_hash) '
                'AND NOT EXISTS (SELECT 1 FROM object_pins op WHERE op.artifact_hash = objects.artifact_hash '
                'AND (op.expires_at IS NULL OR op.expires_at > ?)) '
                'AND NOT EXISTS (SELECT 1 FROM object_leases ol WHERE ol.artifact_hash = objects.artifact_hash '
                'AND ol.expires_at > ?)',
                (now_iso, now_iso, now_iso),
            )
            connection.commit()

    def _delete(self, candidates: list[tuple[str, int]], now: datetime, report: ObjectGCReport) -> None:
        for artifact_hash, size_bytes in candidates:
            if self._has_metadata_reference(artifact_hash, now):
                continue
            try:
                path = self.store.object_path(artifact_hash)
                if not path.exists():
                    report.failures += 1
                    continue
                path.unlink()
                with self.db._connection() as connection:
                    cursor = connection.execute(
                        "DELETE FROM objects WHERE artifact_hash = ? AND gc_state = 'active' "
                        'AND unreferenced_at IS NOT NULL AND unreferenced_at <= ? '
                        'AND NOT EXISTS (SELECT 1 FROM artifact_versions WHERE artifact_hash = ?) '
                        'AND NOT EXISTS (SELECT 1 FROM asset_version_objects WHERE artifact_hash = ?) '
                        'AND NOT EXISTS (SELECT 1 FROM object_pins WHERE artifact_hash = ? '
                        'AND (expires_at IS NULL OR expires_at > ?)) '
                        'AND NOT EXISTS (SELECT 1 FROM object_leases WHERE artifact_hash = ? AND expires_at > ?)',
                        (
                            artifact_hash,
                            _iso(now - timedelta(seconds=self.settings.object_retention_seconds)),
                            artifact_hash,
                            artifact_hash,
                            artifact_hash,
                            _iso(now),
                            artifact_hash,
                            _iso(now),
                        ),
                    )
                    connection.commit()
                if cursor.rowcount:
                    report.objects_deleted += 1
                    report.bytes_reclaimed += size_bytes
            except OSError:
                report.failures += 1

    def _has_metadata_reference(self, artifact_hash: str, now: datetime) -> bool:
        with self.db._connection() as connection:
            row = connection.execute(
                'SELECT 1 FROM artifact_versions WHERE artifact_hash = ? '
                'UNION ALL SELECT 1 FROM asset_version_objects WHERE artifact_hash = ? '
                'UNION ALL SELECT 1 FROM object_pins WHERE artifact_hash = ? '
                'AND (expires_at IS NULL OR expires_at > ?) '
                'UNION ALL SELECT 1 FROM object_leases WHERE artifact_hash = ? AND expires_at > ? LIMIT 1',
                (artifact_hash, artifact_hash, artifact_hash, _iso(now), artifact_hash, _iso(now)),
            ).fetchone()
        return row is not None

    def _reconcile(self, report: ObjectGCReport, *, dry_run: bool) -> None:
        with self.db._connection() as connection:
            rows = connection.execute(
                "SELECT artifact_hash, gc_state FROM objects WHERE gc_state IN ('marked', 'quarantined')"
            ).fetchall()
        for row in rows:
            artifact_hash = str(row['artifact_hash'])
            canonical = self.store.object_path(artifact_hash)
            quarantine = self.store.quarantine_path(artifact_hash)
            if canonical.exists():
                if row['gc_state'] != 'active':
                    if not dry_run:
                        self._activate(artifact_hash)
            elif row['gc_state'] == 'marked' and quarantine.exists():
                if not dry_run:
                    with self.db._connection() as connection:
                        connection.execute(
                            "UPDATE objects SET gc_state = 'quarantined', quarantine_path = ? WHERE artifact_hash = ?",
                            (str(quarantine.relative_to(self.paths.root)), artifact_hash),
                        )
                        connection.commit()
            elif row['gc_state'] == 'quarantined' and not quarantine.exists():
                report.failures += 1

    def _cleanup_temp(self, now: datetime, report: ObjectGCReport) -> None:
        cutoff = now.timestamp() - self.settings.temp_retention_seconds
        for directory in (self.paths.uploads_dir, self.paths.pulled_files_dir, self.paths.worker_temp_dir):
            if not directory.exists():
                continue
            for path in directory.rglob('*'):
                if report.temp_files_deleted >= self.settings.batch_size or not path.is_file():
                    continue
                try:
                    if path.stat().st_mtime <= cutoff:
                        path.unlink()
                        report.temp_files_deleted += 1
                except OSError:
                    report.failures += 1

    def _activate(self, artifact_hash: str) -> None:
        with self.db._connection() as connection:
            connection.execute(
                "UPDATE objects SET gc_state = 'active', gc_marked_at = NULL, quarantined_at = NULL, "
                'delete_after = NULL, quarantine_path = NULL, unreferenced_at = NULL WHERE artifact_hash = ?',
                (artifact_hash,),
            )
            connection.commit()


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace('+00:00', 'Z')
