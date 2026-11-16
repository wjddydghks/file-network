import argparse
import json
from pathlib import Path
import stat

from app.extract_text import positive_integer
from app.incremental_scan import run_incremental_scan
from core.extractors.content import extract_content
from core.operations import check_cancelled
from core.extractors.text import DEFAULT_MAX_BYTES, TEXT_ENCODINGS, TextExtractionResult, validate_text_encoding
from core.scanner.scanner import classify_file
from database import db, file_contents


EXTRACTOR_VERSION = 4


def source_fingerprint(path, max_bytes, text_encoding="auto"):
    metadata = path.stat(follow_symlinks=False)
    return json.dumps({
        "size": metadata.st_size,
        "modified_ns": metadata.st_mtime_ns,
        "device_id": metadata.st_dev,
        "inode": metadata.st_ino,
        "kind": stat.S_IFMT(metadata.st_mode),
        "extension": path.suffix.lower(),
        "max_bytes": max_bytes,
        "extractor_version": EXTRACTOR_VERSION,
        "text_encoding": text_encoding if classify_file(path.suffix) in {"TEXT", "CODE"} else None,
    }, sort_keys=True)


def run_content_analysis(root_path, max_bytes=DEFAULT_MAX_BYTES, is_cancelled=None, progress_callback=None, text_encoding="auto"):
    validate_text_encoding(text_encoding)
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes는 양의 정수여야 합니다.")

    root = Path(root_path).expanduser().resolve()
    scan_options = {}
    if is_cancelled is not None:
        scan_options["is_cancelled"] = is_cancelled
    if progress_callback is not None:
        scan_options["progress_callback"] = progress_callback
    run_incremental_scan(root, **scan_options)
    check_cancelled(is_cancelled)
    file_contents.initialize_file_contents()
    counts = {"extracted": 0, "skipped": 0, "failed": 0, "unchanged": 0}

    files = db.get_active_files_under(root)

    def report_progress():
        if progress_callback is not None:
            progress_callback({"phase": "analysis", "processed": sum(counts.values()), "total": len(files)})

    report_progress()
    for file in files:
        check_cancelled(is_cancelled)
        path = Path(file["path"])
        fingerprint = None
        try:
            fingerprint = source_fingerprint(path, max_bytes, text_encoding)
            previous = file_contents.get_content_state(file["id"])
            if (
                previous
                and previous["status"] in {"EXTRACTED", "SKIPPED"}
                and previous["source_fingerprint"] == fingerprint
            ):
                counts["unchanged"] += 1
                report_progress()
                continue

            result = extract_content(path, max_bytes=max_bytes, text_encoding=text_encoding)
            if source_fingerprint(path, max_bytes, text_encoding) != fingerprint:
                result = TextExtractionResult(
                    str(path), "FAILED", reason="FILE_CHANGED_DURING_ANALYSIS"
                )
        except OSError as error:
            result = TextExtractionResult(
                str(path), "FAILED", reason="READ_ERROR", error=str(error)
            )

        file_contents.save_file_content(file["id"], result, fingerprint)
        counts[result.status.lower()] += 1
        report_progress()

    check_cancelled(is_cancelled)
    return counts


def main():
    parser = argparse.ArgumentParser(description="File Network - Batch Content Analyzer")
    parser.add_argument("path", help="스캔하고 내용을 분석할 디렉터리")
    parser.add_argument("--max-bytes", type=positive_integer, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--encoding", choices=TEXT_ENCODINGS, default="auto", help="텍스트/코드 인코딩: auto(BOM 또는 UTF-8), utf-8, cp949. 폴더의 텍스트 전체에 적용")
    args = parser.parse_args()
    try:
        counts = run_content_analysis(args.path, max_bytes=args.max_bytes, text_encoding=args.encoding)
    except (FileNotFoundError, NotADirectoryError) as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        print("\n작업이 취소되었습니다. 이미 저장된 결과는 유지됩니다.")
        raise SystemExit(130) from None

    print("\n========== 내용 분석 완료 ==========")
    print(json.dumps(counts))
    if counts["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
