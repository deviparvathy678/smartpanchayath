"""Small SQLite helpers for durable officer investigation history.

This module deliberately has no Streamlit dependency. Actor identity remains
NULL unless the host application later adds authenticated sessions.
"""


def ensure_audit_table(connection):
    """Idempotently add the case event journal without altering case records."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS case_audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_type TEXT NOT NULL CHECK(case_type IN ('construction', 'image_detection')),
            case_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT,
            note TEXT,
            resolution_summary TEXT,
            actor_identity TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_case_audit_events_case "
        "ON case_audit_events(case_type, case_id, event_id)"
    )


def record_audit_event(
    connection,
    case_type,
    case_id,
    event_type,
    old_status=None,
    new_status=None,
    note=None,
    resolution_summary=None,
    actor_identity=None,
):
    """Append an event. Never replace earlier notes or status history."""
    if case_type not in ("construction", "image_detection"):
        raise ValueError("Unsupported case type.")
    if not str(case_id or "").strip():
        raise ValueError("Case ID is required for an audit event.")
    if not str(event_type or "").strip():
        raise ValueError("Audit event type is required.")
    connection.execute(
        """
        INSERT INTO case_audit_events (
            case_type, case_id, event_type, old_status, new_status,
            note, resolution_summary, actor_identity
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            case_type,
            str(case_id).strip(),
            str(event_type).strip(),
            old_status,
            new_status,
            str(note).strip() or None if note is not None else None,
            str(resolution_summary).strip() or None
            if resolution_summary is not None
            else None,
            str(actor_identity).strip() or None
            if actor_identity is not None
            else None,
        ),
    )


def validate_resolution_summary(status, summary):
    """Require a decision summary for resolution, without requiring a verdict."""
    if status == "Resolved" and not str(summary or "").strip():
        raise ValueError("Enter a resolution summary before resolving this case.")
