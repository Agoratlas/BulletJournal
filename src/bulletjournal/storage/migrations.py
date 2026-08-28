from __future__ import annotations

import sqlite3
from collections.abc import Callable

MigrationCallable = Callable[[sqlite3.Connection, str, str], None]


def remove_cache_and_nondeterminism(connection: sqlite3.Connection, name: str, applied_at: str) -> None:
    """Apply the objects table rebuild and its marker as one locked transaction."""
    connection.commit()
    connection.execute('PRAGMA foreign_keys = OFF')
    try:
        connection.execute('BEGIN IMMEDIATE')
        if connection.execute('SELECT 1 FROM schema_migrations WHERE name = ?', (name,)).fetchone():
            connection.commit()
            return
        for trigger in (
            'artifact_versions_reference_object',
            'artifact_versions_unreference_object',
            'asset_version_objects_reference_object',
            'asset_version_objects_unreference_object',
            'object_pins_reference_object',
            'object_pins_unreference_object',
            'object_leases_reference_object',
            'object_leases_unreference_object',
        ):
            connection.execute(f'DROP TRIGGER IF EXISTS {trigger}')
        connection.execute('DROP TABLE cache_index')
        object_columns = {
            str(row[1]): str(row[2]) for row in connection.execute('PRAGMA table_info(objects)').fetchall()
        }
        retained_lifecycle_columns = [
            column
            for column in (
                'gc_state',
                'gc_marked_at',
                'quarantined_at',
                'delete_after',
                'quarantine_path',
                'unreferenced_at',
            )
            if column in object_columns
        ]
        lifecycle_definitions = {
            'gc_state': "TEXT NOT NULL DEFAULT 'active'",
            'gc_marked_at': 'TEXT NULL',
            'quarantined_at': 'TEXT NULL',
            'delete_after': 'TEXT NULL',
            'quarantine_path': 'TEXT NULL',
            'unreferenced_at': 'TEXT NULL',
        }
        extra_definitions = ''.join(
            f', {column} {lifecycle_definitions[column]}' for column in retained_lifecycle_columns
        )
        connection.execute(
            f"""
            CREATE TABLE objects_replacement (
                artifact_hash TEXT PRIMARY KEY,
                storage_kind TEXT NOT NULL,
                data_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                extension TEXT NULL,
                mime_type TEXT NULL,
                preview_json TEXT NULL,
                created_at TEXT NOT NULL,
                last_accessed_at TEXT NOT NULL
                {extra_definitions}
            )
            """
        )
        retained_columns = [
            'artifact_hash',
            'storage_kind',
            'data_type',
            'size_bytes',
            'extension',
            'mime_type',
            'preview_json',
            'created_at',
            'last_accessed_at',
            *retained_lifecycle_columns,
        ]
        copied_columns = ', '.join(retained_columns)
        connection.execute(
            f'INSERT INTO objects_replacement ({copied_columns}) SELECT {copied_columns} FROM objects'  # noqa: S608
        )
        connection.execute('DROP TABLE objects')
        connection.execute('ALTER TABLE objects_replacement RENAME TO objects')
        violations = connection.execute('PRAGMA foreign_key_check').fetchall()
        if violations:
            details = ', '.join(f'{row[0]} row {row[1]} -> {row[2]}' for row in violations)
            raise sqlite3.IntegrityError(f'Foreign key check failed after {name}: {details}')
        connection.execute(
            'INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)',
            (name, applied_at),
        )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.execute('PRAGMA foreign_keys = ON')


MIGRATIONS: list[tuple[str, str | MigrationCallable]] = [
    (
        '001_initial',
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS project_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notebook_revisions (
            node_id TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            saved_at TEXT NOT NULL,
            doc_excerpt TEXT NULL,
            interface_json TEXT NOT NULL,
            PRIMARY KEY (node_id, source_hash)
        );

        CREATE TABLE IF NOT EXISTS validation_issues (
            issue_id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS objects (
            artifact_hash TEXT PRIMARY KEY,
            storage_kind TEXT NOT NULL,
            data_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            extension TEXT NULL,
            mime_type TEXT NULL,
            preview_json TEXT NULL,
            created_at TEXT NOT NULL,
            last_accessed_at TEXT NOT NULL,
            nondeterministic INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS artifact_versions (
            version_id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT NOT NULL,
            artifact_name TEXT NOT NULL,
            role TEXT NOT NULL,
            artifact_hash TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            upstream_code_hash TEXT NOT NULL,
            upstream_data_hash TEXT NOT NULL,
            run_id TEXT NOT NULL,
            lineage_mode TEXT NOT NULL,
            created_at TEXT NOT NULL,
            warning_json TEXT NOT NULL DEFAULT '[]',
            FOREIGN KEY (artifact_hash) REFERENCES objects (artifact_hash)
        );

        CREATE TABLE IF NOT EXISTS artifact_heads (
            node_id TEXT NOT NULL,
            artifact_name TEXT NOT NULL,
            current_version_id INTEGER NULL,
            state TEXT NOT NULL,
            PRIMARY KEY (node_id, artifact_name),
            FOREIGN KEY (current_version_id) REFERENCES artifact_versions (version_id)
        );

        CREATE TABLE IF NOT EXISTS run_records (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            target_json TEXT NOT NULL,
            graph_version INTEGER NOT NULL,
            source_snapshot_json TEXT NOT NULL,
            started_at TEXT NULL,
            ended_at TEXT NULL,
            failure_json TEXT NULL
        );

        CREATE TABLE IF NOT EXISTS run_inputs (
            run_id TEXT NOT NULL,
            logical_artifact_id TEXT NOT NULL,
            artifact_hash_at_load TEXT NOT NULL,
            state_at_load TEXT NOT NULL,
            loaded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS run_outputs (
            run_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            artifact_name TEXT NOT NULL,
            version_id INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cache_index (
            node_id TEXT NOT NULL,
            artifact_name TEXT NOT NULL,
            upstream_data_hash TEXT NOT NULL,
            artifact_hash TEXT NOT NULL,
            is_nondeterministic INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (node_id, artifact_name, upstream_data_hash)
        );

        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            graph_version INTEGER NOT NULL,
            path TEXT NOT NULL,
            restored_at TEXT NULL
        );
        """,
    ),
    (
        '002_validation_issue_dismissals',
        """
        CREATE TABLE IF NOT EXISTS validation_issue_dismissals (
            issue_id TEXT PRIMARY KEY,
            dismissed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_validation_issue_dismissals_dismissed_at
        ON validation_issue_dismissals (dismissed_at);
        """,
    ),
    (
        '003_persistent_notices',
        """
        CREATE TABLE IF NOT EXISTS persistent_notices (
            issue_id TEXT PRIMARY KEY,
            node_id TEXT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            dismissed_at TEXT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_persistent_notices_created_at
        ON persistent_notices (created_at DESC);
        """,
    ),
    (
        '004_orchestrator_execution_meta',
        """
        CREATE TABLE IF NOT EXISTS orchestrator_execution_meta (
            node_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT NULL,
            duration_seconds REAL NULL,
            current_cell_json TEXT NULL,
            total_cells INTEGER NULL,
            last_completed_cell_number INTEGER NULL,
            stdout_path TEXT NULL,
            stderr_path TEXT NULL,
            error TEXT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        '005_assets',
        """
        CREATE TABLE IF NOT EXISTS asset_declarations (
            node_id TEXT NOT NULL,
            asset_name TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NULL,
            declared_asset_type TEXT NULL,
            declaration_index INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (node_id, asset_name)
        );

        CREATE TABLE IF NOT EXISTS asset_versions (
            asset_version_id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT NOT NULL,
            asset_name TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            interactive INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            upstream_code_hash TEXT NOT NULL,
            upstream_data_hash TEXT NOT NULL,
            run_id TEXT NOT NULL,
            lineage_mode TEXT NOT NULL,
            definition_json TEXT NOT NULL,
            modifier_schema_json TEXT NOT NULL,
            default_modifiers_json TEXT NOT NULL,
            override_schema_hash TEXT NOT NULL,
            warning_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS asset_heads (
            node_id TEXT NOT NULL,
            asset_name TEXT NOT NULL,
            current_asset_version_id INTEGER NULL,
            state TEXT NOT NULL,
            PRIMARY KEY (node_id, asset_name),
            FOREIGN KEY (current_asset_version_id) REFERENCES asset_versions (asset_version_id)
        );

        CREATE TABLE IF NOT EXISTS asset_version_objects (
            asset_version_id INTEGER NOT NULL,
            object_role TEXT NOT NULL,
            object_index INTEGER NOT NULL DEFAULT 0,
            artifact_hash TEXT NOT NULL,
            metadata_json TEXT NULL,
            PRIMARY KEY (asset_version_id, object_role, object_index),
            FOREIGN KEY (asset_version_id) REFERENCES asset_versions (asset_version_id),
            FOREIGN KEY (artifact_hash) REFERENCES objects (artifact_hash)
        );
        """,
    ),
    (
        '006_notebook_execution_heads',
        """
        CREATE TABLE IF NOT EXISTS notebook_execution_heads (
            node_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            source_hash TEXT NULL,
            upstream_code_hash TEXT NULL,
            upstream_data_hash TEXT NULL,
            run_id TEXT NULL,
            last_run_started_at TEXT NULL,
            last_run_finished_at TEXT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    ('007_remove_cache_and_nondeterminism', remove_cache_and_nondeterminism),
    (
        '008_node_incarnations_and_tombstones',
        """
        CREATE TABLE IF NOT EXISTS node_incarnations (
            incarnation_id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL,
            node_kind TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('live', 'tombstoned', 'expired')),
            generation INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            tombstoned_at TEXT NULL,
            expired_at TEXT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_node_incarnations_one_live_id
        ON node_incarnations(node_id) WHERE status = 'live';

        CREATE TABLE IF NOT EXISTS node_tombstones (
            tombstone_id TEXT PRIMARY KEY,
            incarnation_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            node_kind TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('retained', 'restored', 'expired')),
            deleted_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            restored_at TEXT NULL,
            manifest_path TEXT NOT NULL,
            manifest_checksum TEXT NOT NULL,
            mutation_id TEXT NOT NULL,
            FOREIGN KEY (incarnation_id) REFERENCES node_incarnations (incarnation_id)
        );
        CREATE INDEX IF NOT EXISTS idx_node_tombstones_incarnation
        ON node_tombstones(incarnation_id, deleted_at DESC);

        CREATE TABLE IF NOT EXISTS mutation_requests (
            request_id TEXT PRIMARY KEY,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        ALTER TABLE notebook_revisions ADD COLUMN incarnation_id TEXT NULL;
        ALTER TABLE artifact_versions ADD COLUMN incarnation_id TEXT NULL;
        ALTER TABLE artifact_heads ADD COLUMN incarnation_id TEXT NULL;
        ALTER TABLE asset_declarations ADD COLUMN incarnation_id TEXT NULL;
        ALTER TABLE asset_versions ADD COLUMN incarnation_id TEXT NULL;
        ALTER TABLE asset_heads ADD COLUMN incarnation_id TEXT NULL;
        ALTER TABLE notebook_execution_heads ADD COLUMN incarnation_id TEXT NULL;
        ALTER TABLE orchestrator_execution_meta ADD COLUMN incarnation_id TEXT NULL;
        ALTER TABLE persistent_notices ADD COLUMN incarnation_id TEXT NULL;
        ALTER TABLE run_records ADD COLUMN target_incarnations_json TEXT NULL;
        ALTER TABLE run_outputs ADD COLUMN incarnation_id TEXT NULL;
        ALTER TABLE run_inputs ADD COLUMN producer_incarnation_id TEXT NULL;
        ALTER TABLE run_inputs ADD COLUMN version_id INTEGER NULL;
        """,
    ),
    (
        '010_gc_retention_and_provenance',
        """
        ALTER TABLE run_outputs ADD COLUMN artifact_hash TEXT NULL;
        ALTER TABLE run_outputs ADD COLUMN artifact_role TEXT NULL;
        ALTER TABLE run_outputs ADD COLUMN object_available INTEGER NOT NULL DEFAULT 1;

        CREATE TABLE run_outputs_replacement (
            run_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            artifact_name TEXT NOT NULL,
            version_id INTEGER NULL,
            incarnation_id TEXT NULL,
            artifact_hash TEXT NULL,
            artifact_role TEXT NULL,
            object_available INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO run_outputs_replacement
            (run_id, node_id, artifact_name, version_id, incarnation_id, artifact_hash, artifact_role,
             object_available)
        SELECT ro.run_id, ro.node_id, ro.artifact_name, ro.version_id, ro.incarnation_id,
               av.artifact_hash, av.role, CASE WHEN av.version_id IS NULL THEN 0 ELSE 1 END
        FROM run_outputs ro
        LEFT JOIN artifact_versions av ON av.version_id = ro.version_id;
        DROP TABLE run_outputs;
        ALTER TABLE run_outputs_replacement RENAME TO run_outputs;

        CREATE TABLE IF NOT EXISTS tombstone_expiry_audit (
            tombstone_id TEXT PRIMARY KEY,
            expired_at TEXT NOT NULL,
            audit_json TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS tombstone_expiry_audit_immutable_update
        BEFORE UPDATE ON tombstone_expiry_audit BEGIN
            SELECT RAISE(ABORT, 'tombstone expiry audit is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS tombstone_expiry_audit_immutable_delete
        BEFORE DELETE ON tombstone_expiry_audit BEGIN
            SELECT RAISE(ABORT, 'tombstone expiry audit is immutable');
        END;

        CREATE INDEX IF NOT EXISTS idx_artifact_versions_retention
        ON artifact_versions (incarnation_id, artifact_name, created_at, version_id);
        CREATE INDEX IF NOT EXISTS idx_asset_versions_retention
        ON asset_versions (incarnation_id, asset_name, created_at, asset_version_id);
        CREATE INDEX IF NOT EXISTS idx_run_outputs_version ON run_outputs (version_id);
        CREATE INDEX IF NOT EXISTS idx_run_inputs_run ON run_inputs (run_id);
        CREATE INDEX IF NOT EXISTS idx_node_tombstones_expiry ON node_tombstones (status, expires_at);
        """,
    ),
    (
        '009_publication_batches',
        """
        CREATE TABLE IF NOT EXISTS publication_batches (
            publication_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            incarnation_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            graph_version INTEGER NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('open', 'committed', 'abandoned')),
            created_at TEXT NOT NULL,
            committed_at TEXT NULL,
            abandoned_at TEXT NULL,
            FOREIGN KEY (incarnation_id) REFERENCES node_incarnations (incarnation_id)
        );
        CREATE INDEX IF NOT EXISTS idx_publication_batches_state
        ON publication_batches (state, created_at);

        ALTER TABLE artifact_versions ADD COLUMN publication_id TEXT NULL;
        ALTER TABLE asset_versions ADD COLUMN publication_id TEXT NULL;
        ALTER TABLE run_inputs ADD COLUMN producer_artifact_name TEXT NULL;

        CREATE INDEX IF NOT EXISTS idx_artifact_versions_publication
        ON artifact_versions (publication_id);
        CREATE INDEX IF NOT EXISTS idx_asset_versions_publication
        ON asset_versions (publication_id);
        CREATE INDEX IF NOT EXISTS idx_run_inputs_run
        ON run_inputs (run_id);
        """,
    ),
    (
        '011_publication_scoped_run_inputs',
        """
        ALTER TABLE run_inputs ADD COLUMN publication_id TEXT NULL;
        CREATE INDEX IF NOT EXISTS idx_run_inputs_publication
        ON run_inputs (publication_id);
        """,
    ),
    (
        '012_compressed_mutation_responses',
        """
        ALTER TABLE mutation_requests ADD COLUMN response_zlib BLOB NULL;
        CREATE INDEX IF NOT EXISTS idx_mutation_requests_created_at ON mutation_requests (created_at);
        """,
    ),
]
