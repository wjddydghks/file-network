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
                scanned_at TEXT NOT NULL,
                device_id INTEGER,
                inode INTEGER
            )
        """)

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(files)"
            ).fetchall()
        }

        migrations = {
            "device_id": "ALTER TABLE files ADD COLUMN device_id INTEGER",
            "inode": "ALTER TABLE files ADD COLUMN inode INTEGER"
        }

        for column, sql in migrations.items():
            if column not in columns:
                conn.execute(sql)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_files_name
            ON files(name)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_files_extension
            ON files(extension)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_files_identity
            ON files(device_id, inode)
        """)

        conn.commit()


def save_files(files):
    if not files:
        return 0

    query = """
        INSERT INTO files (
            path, name, extension, size,
            modified_at, created_at, file_type,
            scan_status, scanned_at, device_id, inode
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', ?, ?, ?)

        ON CONFLICT(path) DO UPDATE SET
            name = excluded.name,
            extension = excluded.extension,
            size = excluded.size,
            modified_at = excluded.modified_at,
            created_at = excluded.created_at,
            file_type = excluded.file_type,
            scan_status = 'DISCOVERED',
            scanned_at = excluded.scanned_at,
            device_id = excluded.device_id,
            inode = excluded.inode
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
            file.get("device_id"),
            file.get("inode")
        )
        for file in files
    ]

    with get_connection() as conn:
        conn.executemany(query, values)
        conn.commit()

    return len(values)


def get_file_count():
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()[0]


def get_active_files_under(root_path):
    root = str(Path(root_path).expanduser().resolve())
    prefix = root.rstrip("/") + "/"

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT *
            FROM files
            WHERE scan_status != 'DELETED'
              AND (
                  path = ?
                  OR substr(path, 1, ?) = ?
              )
        """, (root, len(prefix), prefix)).fetchall()

        return [dict(row) for row in rows]


def mark_files_deleted(paths):
    if not paths:
        return 0

    with get_connection() as conn:
        conn.executemany("""
            UPDATE files
            SET scan_status = 'DELETED'
            WHERE path = ?
        """, [(path,) for path in paths])

        conn.commit()

    return len(paths)


def update_moved_file(old_path, new_file):
    with get_connection() as conn:
        cursor = conn.execute("""
            UPDATE files
            SET
                path = ?,
                name = ?,
                extension = ?,
                size = ?,
                modified_at = ?,
                created_at = ?,
                file_type = ?,
                scan_status = 'DISCOVERED',
                scanned_at = ?,
                device_id = ?,
                inode = ?
            WHERE path = ?
              AND scan_status != 'DELETED'
        """, (
            new_file["path"],
            new_file["name"],
            new_file["extension"],
            new_file["size"],
            new_file["modified_at"],
            new_file["created_at"],
            new_file["file_type"],
            new_file["scanned_at"],
            new_file.get("device_id"),
            new_file.get("inode"),
            old_path
        ))

        conn.commit()
        return cursor.rowcount
