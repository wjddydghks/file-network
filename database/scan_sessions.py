
from datetime import datetime, timezone
import uuid

from database import db


def current_time():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def initialize_scan_sessions():
    connection = db.get_connection()

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS scan_sessions (
                id TEXT PRIMARY KEY,
                root_path TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                discovered_count INTEGER NOT NULL DEFAULT 0,
                new_count INTEGER NOT NULL DEFAULT 0,
                updated_count INTEGER NOT NULL DEFAULT 0,
                unchanged_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                scan_complete INTEGER NOT NULL DEFAULT 0
            )
        """)

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(scan_sessions)"
            ).fetchall()
        }

        if "error_count" not in columns:
            connection.execute("""
                ALTER TABLE scan_sessions
                ADD COLUMN error_count INTEGER NOT NULL DEFAULT 0
            """)

        if "scan_complete" not in columns:
            connection.execute("""
                ALTER TABLE scan_sessions
                ADD COLUMN scan_complete INTEGER NOT NULL DEFAULT 0
            """)

        connection.commit()

    finally:
        connection.close()


def start_scan_session(root_path):
    connection = db.get_connection()

    try:
        session_id = str(uuid.uuid4())

        connection.execute("""
            INSERT INTO scan_sessions (
                id,
                root_path,
                status,
                started_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            session_id,
            str(root_path),
            "RUNNING",
            current_time()
        ))

        connection.commit()

        return session_id

    finally:
        connection.close()


def finish_scan_session(
    session_id,
    status,
    discovered_count,
    new_count,
    updated_count,
    unchanged_count,
    error_count=0,
    scan_complete=False
):
    connection = db.get_connection()

    try:
        connection.execute("""
            UPDATE scan_sessions
            SET
                status = ?,
                completed_at = ?,
                discovered_count = ?,
                new_count = ?,
                updated_count = ?,
                unchanged_count = ?,
                error_count = ?,
                scan_complete = ?
            WHERE id = ?
        """, (
            status,
            current_time(),
            discovered_count,
            new_count,
            updated_count,
            unchanged_count,
            error_count,
            int(scan_complete),
            session_id
        ))

        connection.commit()

    finally:
        connection.close()


def get_latest_scan_session():
    connection = db.get_connection()

    try:
        row = connection.execute("""
            SELECT *
            FROM scan_sessions
            ORDER BY started_at DESC
            LIMIT 1
        """).fetchone()

        return dict(row) if row else None

    finally:
        connection.close()

