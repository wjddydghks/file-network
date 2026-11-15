from datetime import datetime, timezone

from database import db


def initialize_file_contents():
    with db.get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS file_contents (
                file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                text TEXT,
                reason TEXT,
                error TEXT,
                source_fingerprint TEXT,
                analyzed_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS invalidate_file_contents
            AFTER UPDATE ON files
            WHEN NEW.scan_status = 'DELETED'
              OR OLD.size IS NOT NEW.size
              OR OLD.modified_at IS NOT NEW.modified_at
              OR OLD.extension IS NOT NEW.extension
              OR OLD.device_id IS NOT NEW.device_id
              OR OLD.inode IS NOT NEW.inode
            BEGIN
                UPDATE file_contents
                SET status = 'STALE', text = NULL, error = NULL,
                    reason = CASE WHEN NEW.scan_status = 'DELETED'
                                  THEN 'FILE_DELETED' ELSE 'FILE_CHANGED' END
                WHERE file_id = NEW.id;
            END
        """)


def get_file_content(file_id):
    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM file_contents WHERE file_id = ?", (file_id,)
        ).fetchone()
        return dict(row) if row else None


def get_content_state(file_id):
    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT status, source_fingerprint FROM file_contents WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        return dict(row) if row else None


def save_file_content(file_id, result, source_fingerprint):
    with db.get_connection() as connection:
        connection.execute("""
            INSERT INTO file_contents (
                file_id, status, text, reason, error, source_fingerprint, analyzed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                status = excluded.status, text = excluded.text,
                reason = excluded.reason, error = excluded.error,
                source_fingerprint = excluded.source_fingerprint,
                analyzed_at = excluded.analyzed_at
        """, (
            file_id, result.status, result.text, result.reason, result.error,
            source_fingerprint, datetime.now(timezone.utc).isoformat(),
        ))
