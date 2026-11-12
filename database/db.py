import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "file_network.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                extension TEXT,
                size INTEGER NOT NULL,
                modified_at TEXT,
                created_at TEXT,
                file_type TEXT,
                scan_status TEXT NOT NULL DEFAULT 'DISCOVERED',
                scanned_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_files_name
            ON files(name)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_files_extension
            ON files(extension)
        """)

        conn.commit()


def save_files(files):
    if not files:
        return 0

    query = """
        INSERT INTO files (
            path, name, extension, size,
            modified_at, created_at, file_type,
            scan_status, scanned_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', ?)

        ON CONFLICT(path) DO UPDATE SET
            name = excluded.name,
            extension = excluded.extension,
            size = excluded.size,
            modified_at = excluded.modified_at,
            created_at = excluded.created_at,
            file_type = excluded.file_type,
            scanned_at = excluded.scanned_at
    """

    values = [
        (
            file["path"],
            file["name"],
            file["extension"],
            file["size"],
            file["modified_at"],
            file["created_at"],
            file["file_type"],
            file["scanned_at"],
        )
        for file in files
    ]

    with get_connection() as conn:
        conn.executemany(query, values)
        conn.commit()

    return len(values)


def get_file_count():
    with get_connection() as conn:
        result = conn.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()

        return result[0]
