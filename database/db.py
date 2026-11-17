import sqlite3
from os import sep
from pathlib import Path
from app.storage import default_database_path, is_frozen


DB_PATH = default_database_path()


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def _store_file_identity(value):
    # 숫자 문자열은 INTEGER 열에서 REAL로 바뀔 수 있어 접두사로 정밀도를 보존한다.
    if isinstance(value, int) and not -(2**63) <= value < 2**63:
        return f"int:{value}"
    return value


def _file_identity_row(cursor, row):
    """파일 식별자를 복원하면서 sqlite3.Row의 이름·인덱스 조회를 유지한다."""
    values = list(row)
    for index, column in enumerate(cursor.description):
        value = values[index]
        if column[0] in ("device_id", "inode") and isinstance(value, str) and value.startswith("int:"):
            values[index] = int(value[4:])
    return sqlite3.Row(cursor, tuple(values))


def get_connection():
    if is_frozen():
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, factory=_ClosingConnection)
    conn.row_factory = _file_identity_row
    conn.execute("PRAGMA foreign_keys = ON")
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
            _store_file_identity(file.get("device_id")),
            _store_file_identity(file.get("inode"))
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


def get_active_file_count():
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM files WHERE scan_status != 'DELETED'"
        ).fetchone()[0]


def get_active_files_under(root_path):
    root = str(Path(root_path).expanduser().resolve())
    prefix = root.rstrip(sep) + sep

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
            _store_file_identity(new_file.get("device_id")),
            _store_file_identity(new_file.get("inode")),
            old_path
        ))

        conn.commit()
        return cursor.rowcount
