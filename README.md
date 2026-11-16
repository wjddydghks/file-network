# File Network

**From file paths to meaningful connections.**

파일을 폴더가 아닌 의미와 관계를 중심으로 탐색하는 시스템

## 1. 프로젝트 소개

기존 파일 시스템에서는 사용자가 파일을 찾기 위해 저장 위치와 폴더 경로를 기억해야 합니다.

File Network는 이러한 문제를 해결하기 위해 파일의 메타데이터와 내용을 분석하고, 파일 간 연관성을 파악하여 관계 중심의 탐색 환경을 구축하는 것을 목표로 합니다.

파일 원본은 기존 파일 시스템에 그대로 유지하며, 별도의 데이터베이스에 파일 정보와 관계를 저장합니다.

### 핵심 목표

- PC에 존재하는 파일을 통합적으로 수집
- 파일명, 경로, 확장자, 크기, 수정 시간 등의 메타데이터 분석
- 지원되는 문서 및 소스 코드의 내용 분석
- 파일 간 의미적 관계 발견
- 키워드 및 의미 기반 검색
- 파일 간 관계를 시각적으로 탐색

## 2. 주요 설계 방향

### 모든 파일을 네트워크에 포함

모든 파일을 네트워크에 등록하되 파일 유형과 접근 권한에 따라 분석 수준을 구분합니다.

| 파일 유형 | 분석 방식 |
|---|---|
| 텍스트 문서 | 메타데이터 및 내용 분석 |
| PDF, DOCX, PPTX, XLSX | 메타데이터 및 내용 분석 |
| 소스 코드 | 메타데이터 및 코드 내용 분석 |
| 이미지 및 동영상 | 파일명 및 메타데이터 분석 |
| 음성 파일 | 파일명 및 메타데이터 분석 |
| 압축 파일 | 파일명 및 메타데이터 분석 |
| 실행 파일 및 바이너리 | 파일명 및 메타데이터 분석 |
| 접근 불가 파일 | 접근 가능한 메타데이터만 분석 |

내용을 분석하지 못하는 파일도 네트워크에서 제외하지 않습니다.

## 3. 기술 스택

| 기술 | 용도 |
|---|---|
| Python | 핵심 엔진 및 파일 처리 |
| SQLite | 파일 메타데이터 및 관계 저장 |
| SQLite FTS5 | 전문 검색 |
| PyMuPDF | PDF 내용 추출 예정 |
| python-docx | DOCX 내용 추출 예정 |
| openpyxl | XLSX 내용 추출 예정 |
| python-pptx | PPTX 내용 추출 예정 |
| Sentence Transformers | 문서 임베딩 및 의미 분석 예정 |
| FAISS | 벡터 검색 예정 |
| Watchdog | 파일 변경 감지 예정 |
| PySide6 | 데스크톱 UI 예정 |
| Cytoscape.js | 관계 그래프 시각화 예정 |

## 4. 프로젝트 구조

```text
file-network/
├── .github/
│   └── workflows/
│       └── ci.yml
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── extract_text.py
│   ├── analyze_content.py
│   ├── search_content.py
│   └── incremental_scan.py
├── core/
│   ├── __init__.py
│   ├── extractors/
│   │   ├── __init__.py
│   │   └── text.py
│   └── scanner/
│       ├── __init__.py
│       └── scanner.py
├── database/
│   ├── __init__.py
│   ├── db.py
│   ├── file_contents.py
│   ├── content_search.py
│   └── scan_sessions.py
├── tests/
├── requirements.txt
├── README.md
└── .gitignore
```

## 5. 실행 방법

### 1) 저장소 복제

```bash
git clone https://github.com/wjddydghks/file-network.git
cd file-network
```

### 2) 가상환경 생성

```bash
python3 -m venv .venv
```

macOS:

```bash
source .venv/bin/activate
```

Windows:

```bash
.venv\Scripts\activate
```

### 3) 프로그램 실행

```bash
mkdir -p ~/file-network-test
echo "Hello File Network" > ~/file-network-test/test.txt
```

```bash
python -m app.main "$HOME/file-network-test"
```

실행 후 `database/file_network.db`에 파일 메타데이터가 저장됩니다.

### 4) 증분 스캔

신규·변경·미변경 파일을 구분하고 이동·삭제를 추적하려면 다음 명령을 사용합니다.

```bash
python -m app.incremental_scan "$HOME/file-network-test"
```

- 매번 전체 디렉터리를 탐색하며, 변경된 파일과 신규 파일만 저장합니다.
- 이동은 스캔 범위 안에서 `(device_id, inode)`가 일대일로 일치하는 경우 판정합니다.
- 삭제는 원본 파일을 지우지 않고 DB 상태를 `DELETED`로 변경합니다.
- 접근 오류가 하나라도 있으면 해당 실행에서는 삭제 판정을 보류합니다.
- 삭제된 경로에 파일이 다시 발견되면 기존 DB 행을 활성화하고 변경 파일로 집계합니다.
- 스캔 결과는 `scan_sessions`에 저장하며 신규·변경·미변경·이동·삭제 건수를 기록합니다.
- 정상 스캔에서 발견한 파일 수는 신규 + 변경 + 미변경 + 이동 건수와 같습니다. 삭제 건수는 발견한 파일 수에 포함하지 않습니다.
- 접근 오류로 삭제 판정을 보류하면 삭제 건수는 0입니다. 실패한 실행의 통계는 실패 시점까지 처리한 결과이며 전체 작업의 성공을 뜻하지 않습니다.
- CLI는 DB 활성 파일 수와 삭제 표시된 행을 포함한 전체 레코드 수를 구분합니다. 두 값은 모든 스캔 경로를 합친 DB 기준이며, 활성 상태는 마지막으로 확인된 상태입니다.
- 기존 DB는 초기화 시 통계 열을 자동 추가합니다. 과거 세션의 이동·삭제 건수는 0으로 초기화되며 소급 계산하지 않습니다.

### 5) 증분 스캔 취소

증분 스캔 중 `Ctrl+C`를 누르면 실행 이력을 `CANCELLED`, `scan_complete=0`으로 종료하고 부분 통계를 기록합니다. CLI는 취소를 안내하고 종료 코드 130을 반환합니다. 탐색 중 취소하면 아직 실행하지 않은 삭제 판정은 진행하지 않습니다.

취소 전에 완료된 DB 변경은 유지되며 전체 실행을 되돌리지는 않습니다. 다음 실행에서 다시 스캔할 수 있습니다. 프로세스 강제 종료나 전원 차단은 이 취소 처리 대상이 아니므로 `RUNNING` 기록이 남을 수 있습니다.

### 6) 테스트

```bash
python -m pip install -r requirements.txt
python -m pytest -v
```

### 7) 텍스트·소스 코드 내용 추출

```bash
python -m app.extract_text "example.txt"
python -m app.extract_text "example.py" --max-bytes 1048576
```

스캐너가 `TEXT` 또는 `CODE`로 분류하는 확장자에 대해 UTF-8 내용을 JSON으로 반환합니다. UTF-8 BOM은 제거하며 줄바꿈과 본문은 유지합니다. JSON의 한글·이모지는 콘솔 인코딩과 무관하게 Unicode 이스케이프로 출력되며 JSON 파싱 시 원문으로 복원됩니다.

| 상태 | 의미 | 종료 코드 |
|---|---|---|
| `EXTRACTED` | `text`에 추출한 본문 저장 | 0 |
| `SKIPPED` | 지원하지 않는 형식, 크기 초과, NUL 포함 바이너리 또는 일반 파일이 아닌 경로 | 0 |
| `FAILED` | UTF-8 디코딩 또는 파일 읽기 실패 | 1 |

`reason`으로 처리 사유를 구분합니다. 크기 제한은 기본 1 MiB이며 BOM을 포함한 원본 바이트 기준입니다. 제한보다 최대 1바이트 더 읽어 크기 초과를 판정합니다. UTF-8 이외의 인코딩을 추측하지 않으며 디렉터리·심볼릭 링크·특수 파일은 열지 않습니다. 잘못된 CLI 인자는 종료 코드 2를 반환합니다.

단일 파일 추출용 CLI이며 원본 파일 내용을 변경하지 않습니다. PDF·Office 파싱은 후속 단계입니다.

### 8) 디렉터리 일괄 내용 분석 및 DB 저장

```bash
python -m app.analyze_content "/분석할/디렉터리"
python -m app.analyze_content "/분석할/디렉터리" --max-bytes 1048576
```

먼저 증분 메타데이터 스캔을 실행한 뒤 해당 범위의 활성 파일을 분석합니다. 결과는 `file_contents` 테이블에 파일 ID별로 저장합니다. 기존 파일·스캔 이력은 유지하며 필요한 테이블과 무효화 트리거를 자동 생성합니다.

- 본문(`text`), 상태(`EXTRACTED`·`SKIPPED`·`FAILED`), 사유·오류, 분석 시각, 원본 fingerprint를 저장합니다.
- fingerprint는 크기, 나노초 단위 수정 시각, 파일 식별자, 파일 종류, 확장자, 크기 제한 및 추출기 버전을 포함합니다. 성공·건너뛰기 결과가 동일하면 재추출을 생략하며 실패한 파일은 다음 실행에서 재시도합니다.
- 이후 메타데이터 스캔에서 파일 변경·삭제가 확인되면 기존 본문을 지우고 `STALE`로 표시합니다. 같은 확장자의 파일 이동은 ID와 분석 결과를 유지합니다.
- 추출 전후 fingerprint가 달라지면 본문을 저장하지 않고 실패 처리합니다. 내용 해시는 사용하지 않으므로 모든 비교 메타데이터가 동일한 내용 변경까지 감지하지는 못합니다.
- 마지막 줄에 `extracted`, `skipped`, `failed`, `unchanged` 건수를 JSON으로 출력합니다. 일부 파일 실패에도 나머지를 계속 분석하며 실패가 있으면 코드 1, 실패가 없으면 0으로 종료합니다.
- `Ctrl+C`는 코드 130으로 종료하며 이미 완료된 스캔·분석 결과를 유지합니다. 재실행하면 저장된 결과를 재사용합니다. 스캔 이력은 메타데이터 스캔의 결과이며 이후 내용 분석의 완료를 의미하지 않습니다.

상태는 마지막 분석 및 스캔에서 확인한 결과이며 실시간 감시가 아닙니다. DB 저장과 분석은 파일별로 진행하므로 실행 전체를 하나의 트랜잭션으로 되돌리지는 않습니다.

### 9) 저장된 파일 본문 검색

```bash
python -m app.search_content "프로젝트"
python -m app.search_content "Python testing" --limit 10 --root "/분석한/디렉터리"
```

먼저 `app.analyze_content`로 내용을 저장한 뒤 검색합니다. 검색 명령은 원본 파일을 스캔하거나 분석하지 않고 DB에 저장된 활성 파일의 `EXTRACTED` 본문을 사용합니다. 결과는 파일 ID·현재 경로·파일명·본문 미리보기(`snippet`)·관련도 점수(`score`)를 포함한 JSON 배열입니다. 미리보기는 일치한 단어를 `[]`로 표시하며 관련도가 높은 결과부터 반환합니다.

- SQLite FTS5의 `unicode61` 토크나이저를 사용합니다. 영문 대소문자를 구분하지 않으며, 한글은 공백·구두점 등으로 나뉜 토큰 기준으로 검색합니다.
- 여러 검색어는 AND로 처리합니다. `OR`, `NOT`, `*`, 인용부호 등의 입력을 FTS 연산자로 실행하지 않습니다. 한국어 형태소 분석·임의 부분 문자열·접두사·의미 검색은 지원하지 않습니다.
- 검색 결과 수는 기본 20개, 최대 1000개입니다. `--root`는 해당 디렉터리 하위로 범위를 제한합니다. 파일명·경로 자체를 검색하는 기능은 포함하지 않습니다.
- 처음 실행할 때 기존 본문을 색인하고, 이후 분석 결과 삽입·갱신·삭제 및 스캔에 따른 본문 무효화를 트리거로 반영합니다. 이동된 파일은 현재 경로로 표시하며 삭제·`STALE`·`SKIPPED`·`FAILED` 결과는 제외합니다.
- 결과가 없어도 코드 0과 빈 배열을 반환합니다. 잘못된 검색어·결과 수는 코드 2, FTS5 미지원·DB 오류는 코드 1을 반환합니다.

원본 변경은 메타데이터 스캔 및 재분석 후 반영됩니다. 이 검색은 마지막으로 저장한 본문을 조회하며 실시간 파일 상태를 확인하지 않습니다.

## 6. 개발 현황

- [x] Python 프로젝트 초기 구조 구성
- [x] SQLite 데이터베이스 초기화
- [x] 디렉터리 재귀 탐색
- [x] 파일 메타데이터 수집
- [x] 파일 유형 분류
- [x] 배치 단위 데이터베이스 저장
- [x] 동일 경로 파일 정보 갱신

## 7. 개발 로드맵

### Phase 1. 파일 스캔 안정화

- [x] 파일 이동 및 삭제 감지
- [x] 파일 이동 추적용 식별자 도입 (`device_id`, `inode`)
- [x] 신규·변경 파일만 DB 저장
- [ ] 변경된 파일만 내용 재분석 (내용 분석 구현 후)
- [ ] 스캔 진행률 및 취소 기능
- [ ] macOS 및 Windows 호환성 개선

### Phase 2. 파일 내용 분석

- [x] TEXT·CODE 파일의 UTF-8 내용 추출 및 단일 파일 CLI
- [x] 내용 추출 결과 DB 저장 및 증분 일괄 분석
- [ ] PDF, DOCX, PPTX, XLSX 파서 구현
- [ ] 소스 코드 분석
- [ ] 파일별 키워드 및 주제 추출
- [x] 파일 분석 상태 및 무효화 관리

### Phase 3. 관계 분석 및 검색

- [ ] 파일 간 관계 데이터 모델링
- [ ] 관계 유형 및 신뢰도 저장
- [x] SQLite FTS5 본문 검색 및 CLI
- [ ] 임베딩 기반 의미 검색
- [ ] 자동 관계 발견 및 갱신

### Phase 4. UI 및 시각화

- [ ] PySide6 기반 데스크톱 UI
- [ ] 검색 중심 파일 탐색
- [ ] 파일 관계 그래프 시각화
- [ ] 관계 근거 및 신뢰도 표시

### Phase 5. 플랫폼 확장

- [ ] macOS 지원
- [ ] Windows 지원
- [ ] OS별 파일 시스템 어댑터 분리

## 8. Git 협업 규칙

프로젝트의 변경 사항은 Issue를 기반으로 관리하며, 모든 기능 개발 및 수정은 별도 브랜치에서 진행합니다.

### 8.1 Issue 작성 규칙

새로운 기능 개발, 버그 수정, 개선 사항은 Issue를 먼저 생성합니다.

Issue 제목은 다음 형식을 사용합니다.

```text
[Type] 작업 내용
```

사용하는 Type:

| Type | 설명 |
|---|---|
| Feature | 신규 기능 개발 |
| Bug | 버그 수정 |
| Refactor | 코드 구조 개선 |
| Test | 테스트 코드 작성 및 개선 |
| CI | CI/CD 및 자동화 구성 |
| Docs | 문서 작성 및 수정 |
| Chore | 기타 설정 및 유지보수 |

Issue에는 다음 내용을 포함합니다.

- **Background:** 작업이 필요한 이유
- **Goal:** 달성하고자 하는 목표
- **Tasks:** 구체적인 작업 목록
- **Acceptance Criteria:** 완료 조건

작업 완료 여부를 판단할 수 있도록 가능한 한 구체적으로 작성합니다.

### 8.2 Branch 규칙

브랜치 이름은 다음 형식을 사용합니다.

```text
type/issue-number-description
```

예시:

```text
feature/12-content-analyzer
fix/15-symlink-scan
test/18-scanner-test
ci/1-github-actions
docs/20-readme-update
```

- `main` 브랜치에 직접 작업하지 않습니다.
- 모든 작업은 Issue에 연결된 별도 브랜치에서 진행합니다.
- 브랜치 이름에는 관련 Issue 번호를 포함합니다.

### 8.3 Commit 규칙

커밋 메시지는 다음 형식을 사용합니다.

```text
type: 작업 내용
```

예시:

```text
feat: add file metadata scanner
fix: handle inaccessible directories
refactor: separate file classification logic
test: add scanner unit tests
ci: configure github actions
docs: update project readme
chore: update project configuration
```

| Type | 설명 |
|---|---|
| feat | 신규 기능 |
| fix | 버그 수정 |
| refactor | 코드 리팩토링 |
| test | 테스트 코드 |
| ci | CI 설정 |
| docs | 문서 수정 |
| chore | 기타 설정 |

하나의 커밋에는 가능한 한 하나의 목적만 포함합니다.

### 8.4 Pull Request 규칙

모든 변경 사항은 Pull Request를 통해 `main` 브랜치에 반영합니다.

PR 제목은 다음 형식을 사용합니다.

```text
[Type] 작업 내용
```

PR 본문에는 다음 항목을 포함합니다.

- **Related Issue:** 연결된 Issue 번호
- **Changes:** 주요 변경 사항
- **Test:** 수행한 테스트 및 결과
- **Checklist:** 완료 여부 확인

예시:

```text
[CI] GitHub Actions 기반 자동 검사 구성
```

PR 작성 시 `Closes #이슈번호`를 사용하여 관련 Issue와 연결합니다.

### 8.5 Merge 규칙

- CI 검사를 통과한 PR만 Merge합니다.
- 변경 사항을 확인한 후 Merge합니다.
- 작업이 완료되면 관련 Issue를 종료합니다.
- 불필요한 임시 파일이나 데이터베이스 파일은 커밋하지 않습니다.

## 9. CI 정책

GitHub Actions를 사용하여 코드 변경 시 자동으로 검증합니다.

### 실행 조건

- `main` 브랜치에 Push
- `main` 브랜치를 대상으로 하는 Pull Request

### 검증 항목

- Python 문법 검사
- 주요 모듈 Import 검사
- 단위 테스트
- 데이터베이스 및 파일 스캐너 검증

Ubuntu 24.04, macOS 15, Windows Server 2022에서 Python 3.12로 문법 검사와 전체 테스트를 실행합니다.

## 10. 개발 원칙

1. 기존 파일 시스템과 원본 파일을 변경하지 않습니다.
2. 모든 파일을 동일한 수준으로 분석하지 않습니다.
3. 파일 분석 결과와 관계의 근거를 추적할 수 있도록 설계합니다.
4. 의미적 유사성과 명시적인 의존 관계를 구분합니다.
5. 초기에는 Python으로 기능을 검증하고, 성능 병목이 확인되면 필요한 부분을 최적화합니다.

---

**File Network**

*From file paths to meaningful connections.*
