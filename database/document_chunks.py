"""분석 스냅샷에 연결된 로컬 인용 조회. 원본 파일은 열지 않는다."""
from database import db, file_contents


def get_document_chunk(file_id, segment=0, analyzed_at=None):
    if type(file_id) is not int or not 1 <= file_id < 2**63:
        raise ValueError("올바른 파일 ID를 지정하세요.")
    if type(segment) is not int or not 0 <= segment < 2**63:
        raise ValueError("올바른 인용 번호를 지정하세요.")
    db.initialize_database()
    file_contents.initialize_file_contents()
    # 한 SELECT의 읽기 스냅샷에서 본문·분석 버전·인용 개수를 함께 얻는다.
    with db.get_connection() as connection:
        row = connection.execute("""
            SELECT files.id AS file_id, files.name, files.path,
                   COALESCE(fc.status, 'PENDING') AS status,
                   fc.reason, fc.error, fc.analyzed_at, fc.source_fingerprint,
                   COALESCE(substr(fc.text, chunk.start_char + 1,
                       chunk.end_char - chunk.start_char), '') AS text,
                   chunk.start_char, chunk.end_char, chunk.page,
                   COALESCE(chunk.source_kind, CASE WHEN chunk.page IS NOT NULL THEN 'page'
                       WHEN chunk.chunk_index IS NOT NULL THEN 'text' END) AS source_kind,
                   chunk.source_label,
                   CASE WHEN fc.status = 'EXTRACTED' THEN COALESCE(length(fc.text), 0)
                        ELSE 0 END AS total_chars,
                   (SELECT COUNT(*) FROM document_chunks c
                    WHERE c.file_id = fc.file_id AND fc.status = 'EXTRACTED') AS chunk_count
            FROM files LEFT JOIN file_contents fc ON fc.file_id = files.id
            LEFT JOIN document_chunks chunk
                ON chunk.file_id = fc.file_id AND chunk.chunk_index = ?
                AND fc.status = 'EXTRACTED'
            WHERE files.id = ? AND files.scan_status != 'DELETED'
        """, (segment, file_id)).fetchone()
    if row is None:
        return None
    result = dict(row)
    if analyzed_at is not None and result['analyzed_at'] != analyzed_at:
        raise ValueError("분석 결과가 변경되었습니다. 처음부터 새로 읽기를 누르세요.")
    if result['chunk_count'] and segment >= result['chunk_count']:
        raise ValueError("존재하지 않는 인용 번호입니다. 처음부터 새로 읽기를 누르세요.")
    result.update(
        segment=segment if result['chunk_count'] else 0,
        segment_count=max(1, result['chunk_count']),
        chunk_mode=True,
    )
    return result
