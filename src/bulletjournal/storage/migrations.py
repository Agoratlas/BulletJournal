from __future__ import annotations


MIGRATIONS: list[tuple[str, str]] = [
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

        CREATE TABLE IF NOT EXISTS artifact_objects (
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
            FOREIGN KEY (artifact_hash) REFERENCES artifact_objects (artifact_hash)
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
]
