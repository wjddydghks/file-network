"""분석 상태·사유를 GUI에서 읽기 쉬운 설명으로 표시한다."""

STATUS_LABELS = {
    "EXTRACTED": "분석 완료", "SKIPPED": "건너뜀", "FAILED": "실패",
    "STALE": "재분석 필요", "PENDING": "미분석",
}

REASON_LABELS = {
    "UNSUPPORTED_TYPE": "지원하지 않는 형식",
    "TOO_LARGE": "파일 크기 제한 초과",
    "BINARY_CONTENT": "텍스트가 아닌 바이너리 내용",
    "INVALID_ENCODING": "UTF-8 인코딩이 아님",
    "READ_ERROR": "파일 읽기 실패",
    "NOT_REGULAR_FILE": "일반 파일이 아닌 경로",
    "DEPENDENCY_MISSING": "분석 라이브러리 설치 필요",
    "ENCRYPTED_PDF": "암호가 필요한 PDF",
    "INVALID_PDF": "PDF 읽기·해석 실패",
    "OCR_REQUIRED": "이미지 PDF — OCR 필요",
    "PARTIAL_OCR_REQUIRED": "일부 이미지 페이지 — OCR 필요",
    "NO_TEXT": "추출할 텍스트 없음",
    "PAGE_LIMIT_EXCEEDED": "페이지 수 제한 초과",
    "TEXT_LIMIT_EXCEEDED": "추출 본문 길이 제한 초과",
    "FILE_CHANGED_DURING_ANALYSIS": "분석 도중 파일 변경",
    "FILE_CHANGED": "스캔에서 파일 변경 확인",
    "FILE_DELETED": "스캔에서 파일 삭제 확인",
    "INVALID_OFFICE": "Office 문서 읽기·해석 실패",
    "ENCRYPTED_OFFICE": "암호화된 Office 문서",
    "ENCRYPTED_OR_LEGACY_OFFICE": "암호화 또는 구형 Office 컨테이너",
    "ARCHIVE_SIZE_LIMIT_EXCEEDED": "압축 해제 용량 제한 초과",
    "ARCHIVE_MEMBER_LIMIT_EXCEEDED": "문서 내부 파일 수 제한 초과",
    "CELL_LIMIT_EXCEEDED": "스프레드시트 셀 범위 제한 초과",
    "SLIDE_LIMIT_EXCEEDED": "슬라이드 수 제한 초과",
    "STRUCTURE_LIMIT_EXCEEDED": "문서 중첩 구조 제한 초과",
}

REASON_GUIDANCE = {
    "UNSUPPORTED_TYPE": "현재 이 형식은 본문 추출을 지원하지 않습니다. 원본 파일은 열 수 있습니다.",
    "TOO_LARGE": "파일 크기 제한을 늘린 뒤 스캔·분석을 다시 실행하세요.",
    "INVALID_ENCODING": "현재 텍스트 추출은 UTF-8을 지원합니다. 필요한 경우 UTF-8 사본을 만들어 분석하세요.",
    "READ_ERROR": "파일이 존재하는지와 읽기 권한을 확인한 뒤 다시 분석하세요.",
    "DEPENDENCY_MISSING": "README의 분석 의존성 설치 방법을 확인하세요.",
    "ENCRYPTED_PDF": "현재 PDF 암호 입력을 지원하지 않습니다.",
    "OCR_REQUIRED": "이미지에 있는 글자는 아직 추출하지 않습니다. 현재 OCR 기능은 제공하지 않습니다.",
    "PARTIAL_OCR_REQUIRED": "읽을 수 있는 텍스트만 저장했습니다. 이미지 전용 페이지의 글자는 검색되지 않습니다.",
    "FILE_CHANGED_DURING_ANALYSIS": "파일 수정이 끝난 뒤 스캔·분석을 다시 실행하세요.",
    "FILE_CHANGED": "기존 본문은 무효화되었습니다. 스캔·분석을 다시 실행하세요.",
    "INVALID_OFFICE": "파일이 실제 DOCX·XLSX·PPTX 형식인지, 손상되지 않았는지 확인하세요.",
    "ENCRYPTED_OFFICE": "현재 Office 암호 입력은 지원하지 않습니다.",
    "ENCRYPTED_OR_LEGACY_OFFICE": "암호화된 문서이거나 구형 OLE 형식입니다. 현재 암호 입력과 구형 형식 변환은 지원하지 않습니다.",
}


def reason_label(row):
    reason = row.get("reason")
    if reason:
        return REASON_LABELS.get(reason, reason)
    return "아직 본문을 분석하지 않음" if row["status"] == "PENDING" else "—"


def format_analysis_detail(row):
    lines = [
        row["name"], row["path"], "",
        f"상태: {STATUS_LABELS.get(row['status'], row['status'])}",
        f"사유: {reason_label(row)}",
    ]
    if row.get("reason") in REASON_GUIDANCE:
        lines.extend(("", REASON_GUIDANCE[row["reason"]]))
    elif row["status"] in {"PENDING", "STALE"}:
        lines.extend(("", "본문 검색에 포함하려면 스캔·분석을 실행하세요."))
    if row.get("error"):
        lines.extend(("", "오류 상세:", row["error"]))
    if row.get("analyzed_at"):
        lines.extend(("", f"마지막 분석 시각: {row['analyzed_at']}"))
    return "\n".join(lines)
