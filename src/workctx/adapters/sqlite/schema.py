"""Projection schema owned entirely by the SQLite adapter."""

from __future__ import annotations

import sqlite3

PROJECTION_SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE projection_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    projection_schema_version INTEGER NOT NULL,
    workspace_schema_version INTEGER NOT NULL,
    context_id TEXT NOT NULL,
    context_updated_at TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL CHECK (length(source_fingerprint) = 64),
    source_file_count INTEGER NOT NULL CHECK (source_file_count >= 0),
    indexed_document_count INTEGER NOT NULL CHECK (indexed_document_count >= 0),
    skipped_document_count INTEGER NOT NULL CHECK (skipped_document_count >= 0),
    build_started_at TEXT NOT NULL,
    build_completed_at TEXT NOT NULL
);

CREATE TABLE entities (
    context_id TEXT NOT NULL,
    id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    uri TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT,
    confidence TEXT,
    body TEXT NOT NULL,
    source_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (context_id, id),
    UNIQUE (context_id, uri)
);

CREATE TABLE aliases (
    context_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    alias TEXT NOT NULL,
    PRIMARY KEY (context_id, entity_id, position),
    UNIQUE (context_id, entity_id, alias),
    FOREIGN KEY (context_id, entity_id) REFERENCES entities(context_id, id) ON DELETE CASCADE
);

CREATE TABLE entity_tags (
    context_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    tag TEXT NOT NULL,
    PRIMARY KEY (context_id, entity_id, position),
    UNIQUE (context_id, entity_id, tag),
    FOREIGN KEY (context_id, entity_id) REFERENCES entities(context_id, id) ON DELETE CASCADE
);

CREATE TABLE edges (
    edge_id INTEGER PRIMARY KEY,
    context_id TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    relation TEXT NOT NULL,
    target_uri TEXT NOT NULL,
    confidence TEXT,
    valid_from TEXT,
    valid_to TEXT,
    note TEXT,
    source_path TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    UNIQUE (context_id, source_uri, source_path, ordinal)
);

CREATE TABLE edge_source_observations (
    edge_id INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    observation_uri TEXT NOT NULL,
    PRIMARY KEY (edge_id, position),
    UNIQUE (edge_id, observation_uri),
    FOREIGN KEY (edge_id) REFERENCES edges(edge_id) ON DELETE CASCADE
);

CREATE VIEW backlinks AS
SELECT
    edge_id,
    context_id,
    target_uri,
    source_uri,
    relation,
    confidence,
    valid_from,
    valid_to,
    note,
    source_path,
    ordinal
FROM edges;

CREATE TABLE observations (
    context_id TEXT NOT NULL,
    id TEXT NOT NULL,
    uri TEXT NOT NULL,
    parent_entity_uri TEXT,
    kind TEXT NOT NULL,
    statement TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    locator_type TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    observed_at TEXT,
    valid_from TEXT,
    valid_to TEXT,
    body TEXT NOT NULL,
    source_path TEXT NOT NULL,
    PRIMARY KEY (context_id, id),
    UNIQUE (context_id, uri)
);

CREATE TABLE observation_derivations (
    context_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    source_reference TEXT NOT NULL,
    PRIMARY KEY (context_id, observation_id, position),
    UNIQUE (context_id, observation_id, source_reference),
    FOREIGN KEY (context_id, observation_id)
        REFERENCES observations(context_id, id) ON DELETE CASCADE
);

CREATE TABLE claims (
    context_id TEXT NOT NULL,
    id TEXT NOT NULL,
    uri TEXT NOT NULL,
    subject_uri TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    status TEXT NOT NULL,
    supersedes TEXT,
    superseded_by TEXT,
    confidence TEXT NOT NULL,
    body TEXT NOT NULL,
    source_path TEXT NOT NULL,
    PRIMARY KEY (context_id, id),
    UNIQUE (context_id, uri)
);

CREATE TABLE claim_source_observations (
    context_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    observation_uri TEXT NOT NULL,
    PRIMARY KEY (context_id, claim_id, position),
    UNIQUE (context_id, claim_id, observation_uri),
    FOREIGN KEY (context_id, claim_id) REFERENCES claims(context_id, id) ON DELETE CASCADE
);

CREATE TABLE tasks (
    context_id TEXT NOT NULL,
    id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    parent_task TEXT,
    root_task TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    owner TEXT,
    requester TEXT,
    due_at TEXT,
    next_action TEXT NOT NULL,
    PRIMARY KEY (context_id, id),
    FOREIGN KEY (context_id, id) REFERENCES entities(context_id, id) ON DELETE CASCADE
);

CREATE TABLE task_waiting_on (
    context_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    value TEXT NOT NULL,
    PRIMARY KEY (context_id, task_id, position),
    UNIQUE (context_id, task_id, value),
    FOREIGN KEY (context_id, task_id) REFERENCES tasks(context_id, id) ON DELETE CASCADE
);

CREATE TABLE task_dependencies (
    context_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    value TEXT NOT NULL,
    PRIMARY KEY (context_id, task_id, position),
    UNIQUE (context_id, task_id, value),
    FOREIGN KEY (context_id, task_id) REFERENCES tasks(context_id, id) ON DELETE CASCADE
);

CREATE TABLE task_blockers (
    context_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    value TEXT NOT NULL,
    PRIMARY KEY (context_id, task_id, position),
    UNIQUE (context_id, task_id, value),
    FOREIGN KEY (context_id, task_id) REFERENCES tasks(context_id, id) ON DELETE CASCADE
);

CREATE TABLE task_source_observations (
    context_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    value TEXT NOT NULL,
    PRIMARY KEY (context_id, task_id, position),
    UNIQUE (context_id, task_id, value),
    FOREIGN KEY (context_id, task_id) REFERENCES tasks(context_id, id) ON DELETE CASCADE
);

CREATE TABLE search_documents (
    rowid INTEGER PRIMARY KEY,
    context_id TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    record_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    uri TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    statement TEXT NOT NULL,
    source_path TEXT NOT NULL,
    UNIQUE (context_id, record_kind, record_id)
);

CREATE VIRTUAL TABLE search_fts USING fts5(
    title,
    body,
    statement,
    content='search_documents',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE INDEX entities_by_type ON entities(context_id, entity_type, id);
CREATE INDEX aliases_by_value ON aliases(context_id, alias, entity_id);
CREATE INDEX edges_by_source ON edges(context_id, source_uri, relation, ordinal);
CREATE INDEX edges_by_target ON edges(context_id, target_uri, relation, source_uri);
CREATE INDEX observations_by_parent ON observations(context_id, parent_entity_uri, id);
CREATE INDEX claims_by_subject ON claims(context_id, subject_uri, status, observed_at, id);
CREATE INDEX claims_by_supersedes ON claims(context_id, supersedes);
CREATE INDEX tasks_by_state ON tasks(context_id, status, priority, id);
CREATE INDEX tasks_by_root ON tasks(context_id, root_task, parent_task, id);
CREATE INDEX task_waiting_by_value ON task_waiting_on(context_id, value, task_id);
"""

_CONTEXT_GUARDED_TABLES = (
    "entities",
    "aliases",
    "entity_tags",
    "edges",
    "observations",
    "observation_derivations",
    "claims",
    "claim_source_observations",
    "tasks",
    "task_waiting_on",
    "task_dependencies",
    "task_blockers",
    "task_source_observations",
    "search_documents",
)

_REQUIRED_TABLES = frozenset(
    {
        "projection_metadata",
        "entities",
        "aliases",
        "entity_tags",
        "edges",
        "edge_source_observations",
        "observations",
        "observation_derivations",
        "claims",
        "claim_source_observations",
        "tasks",
        "task_waiting_on",
        "task_dependencies",
        "task_blockers",
        "task_source_observations",
        "search_documents",
        "search_fts",
    }
)
_REQUIRED_INDEXES = frozenset(
    {
        "entities_by_type",
        "aliases_by_value",
        "edges_by_source",
        "edges_by_target",
        "observations_by_parent",
        "claims_by_subject",
        "claims_by_supersedes",
        "tasks_by_state",
        "tasks_by_root",
        "task_waiting_by_value",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    f"guard_{table}_context_{operation}"
    for table in _CONTEXT_GUARDED_TABLES
    for operation in ("insert", "update")
)


def create_schema(connection: sqlite3.Connection) -> None:
    """Create the current projection schema on an empty database."""

    connection.executescript(_SCHEMA_SQL)
    for table in _CONTEXT_GUARDED_TABLES:
        connection.executescript(
            f"""
            CREATE TRIGGER guard_{table}_context_insert
            BEFORE INSERT ON {table}
            WHEN NOT EXISTS (
                SELECT 1 FROM projection_metadata WHERE singleton = 1
            ) OR NEW.context_id != (
                SELECT context_id FROM projection_metadata WHERE singleton = 1
            )
            BEGIN
                SELECT RAISE(ABORT, 'context isolation violation');
            END;

            CREATE TRIGGER guard_{table}_context_update
            BEFORE UPDATE OF context_id ON {table}
            WHEN NOT EXISTS (
                SELECT 1 FROM projection_metadata WHERE singleton = 1
            ) OR NEW.context_id != (
                SELECT context_id FROM projection_metadata WHERE singleton = 1
            )
            BEGIN
                SELECT RAISE(ABORT, 'context isolation violation');
            END;
            """
        )
    connection.execute(f"PRAGMA user_version = {PROJECTION_SCHEMA_VERSION}")


def schema_is_compatible(connection: sqlite3.Connection) -> bool:
    """Return whether all version-1 query and isolation objects are present."""

    objects = {
        (str(row[0]), str(row[1]))
        for row in connection.execute("SELECT type, name FROM sqlite_schema").fetchall()
    }
    required = {
        *(("table", name) for name in _REQUIRED_TABLES),
        *(("view", "backlinks"),),
        *(("index", name) for name in _REQUIRED_INDEXES),
        *(("trigger", name) for name in _REQUIRED_TRIGGERS),
    }
    if not required.issubset(objects):
        return False
    try:
        connection.execute("SELECT rowid FROM search_fts LIMIT 0").fetchall()
    except sqlite3.Error:
        return False
    return True
