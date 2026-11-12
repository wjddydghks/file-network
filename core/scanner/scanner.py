import os
from pathlib import Path
from datetime import datetime, timezone


def classify_file(extension):
    extension = extension.lower()

    text_extensions = {
        ".txt", ".md", ".csv", ".json", ".xml",
        ".yaml", ".yml", ".log"
    }

    document_extensions = {
        ".pdf", ".docx", ".doc",
        ".pptx", ".ppt", ".xlsx", ".xls"
    }

    code_extensions = {
        ".py", ".java", ".js", ".ts",
        ".html", ".css", ".c", ".cpp",
        ".h", ".sql", ".kt", ".swift"
    }

    image_extensions = {
        ".jpg", ".jpeg", ".png", ".gif",
        ".webp", ".bmp", ".svg", ".heic"
    }

    video_extensions = {
        ".mp4", ".mov", ".avi", ".mkv",
        ".wmv", ".webm"
    }

    audio_extensions = {
        ".mp3", ".wav", ".flac", ".aac",
        ".m4a", ".ogg"
    }

    archive_extensions = {
        ".zip", ".rar", ".7z", ".tar", ".gz"
    }

    if extension in text_extensions:
        return "TEXT"
    if extension in document_extensions:
        return "DOCUMENT"
    if extension in code_extensions:
        return "CODE"
    if extension in image_extensions:
        return "IMAGE"
    if extension in video_extensions:
        return "VIDEO"
    if extension in audio_extensions:
        return "AUDIO"
    if extension in archive_extensions:
        return "ARCHIVE"

    return "OTHER"


def scan_directory(root_path, batch_size=500):
    root = Path(root_path).expanduser().resolve()

    if not root.exists():
        raise FileNotFoundError(f"경로가 존재하지 않습니다: {root}")

    if not root.is_dir():
        raise NotADirectoryError(f"디렉터리가 아닙니다: {root}")

    stack = [str(root)]
    batch = []

    scanned_count = 0
    error_count = 0

    while stack:
        current_path = stack.pop()

        try:
            with os.scandir(current_path) as entries:
                for entry in entries:
                    try:
                        # 심볼릭 링크는 탐색하지 않음
                        if entry.is_symlink():
                            continue

                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue

                        if not entry.is_file(follow_symlinks=False):
                            continue

                        stat = entry.stat(
                            follow_symlinks=False
                        )

                        extension = Path(entry.name).suffix.lower()

                        now = datetime.now(timezone.utc).isoformat()

                        batch.append({
                            "path": os.path.abspath(entry.path),
                            "name": entry.name,
                            "extension": extension,
                            "size": stat.st_size,
                            "modified_at": datetime.fromtimestamp(
                                stat.st_mtime,
                                timezone.utc
                            ).isoformat(),
                            "created_at": datetime.fromtimestamp(
                                stat.st_birthtime,
                                timezone.utc
                            ).isoformat()
                            if hasattr(stat, "st_birthtime")
                            else None,
                            "file_type": classify_file(extension),
                            "scanned_at": now,
                        })

                        scanned_count += 1

                        if len(batch) >= batch_size:
                            yield batch
                            batch = []

                    except OSError:
                        error_count += 1

        except OSError:
            error_count += 1

    if batch:
        yield batch

    print(f"발견한 파일: {scanned_count:,}개")
    print(f"접근 오류: {error_count:,}건")
