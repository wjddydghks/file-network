
import uuid
from datetime import datetime, timezone

from database import db


def current_time():
	return datetime.now(timezone.utc).astimezone().isoformat(timespec="microseconds")

def initialize_scan_sessions():
    """스캔 이력을 저장할 테이블을 생성한다."""

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
                unchanged_count INTEGER NOT NULL DEFAULT 0
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_scan_sessions_root_path
            ON scan_sessions(root_path)
        """)

        connection.commit()

    finally:
        connection.close()


def start_scan_session(root_path):
    """새로운 스캔 세션을 시작하고 ID를 반환한다."""

    session_id = str(uuid.uuid4())

    connection = db.get_connection()

    try:
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
            root_path,
            "RUNNING",
            current_time()
        ))

        connection.commit()

    finally:
        connection.close()

    return session_id


def finish_scan_session(
    session_id,
    status,
    discovered_count,
    new_count,
    updated_count,
    unchanged_count
):
    """스캔 종료 상태와 결과를 기록한다."""

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
                unchanged_count = ?
            WHERE id = ?
        """, (
            status,
            current_time(),
            discovered_count,
            new_count,
            updated_count,
            unchanged_count,
            session_id
        ))

        connection.commit()

    finally:
        connection.close()


def get_latest_scan_session():
    """가장 최근 스캔 이력을 조회한다."""

    connection = db.get_connection()

    try:
        return connection.execute("""
            SELECT *
            FROM scan_sessions
            ORDER BY started_at DESC
            LIMIT 1
        """).fetchone()

    finally:
        connection.close()

