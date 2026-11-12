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
│   └── main.py
├── core/
│   ├── __init__.py
│   └── scanner/
│       ├── __init__.py
│       └── scanner.py
├── database/
│   ├── __init__.py
│   └── db.py
├── tests/
├── requirements.txt
├── README.md
└── .gitignore
```

## 5. 실행 방법

### 1) 저장소 복제

```bash
git clone https://github.com/사용자명/file-network.git
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

- [ ] 파일 이동 및 삭제 감지
- [ ] 파일 고유 식별자 도입
- [ ] 변경된 파일만 재분석
- [ ] 스캔 진행률 및 취소 기능
- [ ] macOS 및 Windows 호환성 개선

### Phase 2. 파일 내용 분석

- [ ] PDF, DOCX, PPTX, XLSX 파서 구현
- [ ] 소스 코드 분석
- [ ] 파일별 키워드 및 주제 추출
- [ ] 파일 분석 상태 관리

### Phase 3. 관계 분석 및 검색

- [ ] 파일 간 관계 데이터 모델링
- [ ] 관계 유형 및 신뢰도 저장
- [ ] SQLite FTS5 검색
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

향후 macOS와 Windows 환경까지 테스트 범위를 확장합니다.

## 10. 개발 원칙

1. 기존 파일 시스템과 원본 파일을 변경하지 않습니다.
2. 모든 파일을 동일한 수준으로 분석하지 않습니다.
3. 파일 분석 결과와 관계의 근거를 추적할 수 있도록 설계합니다.
4. 의미적 유사성과 명시적인 의존 관계를 구분합니다.
5. 초기에는 Python으로 기능을 검증하고, 성능 병목이 확인되면 필요한 부분을 최적화합니다.

---

**File Network**

*From file paths to meaningful connections.*# File Network

**파일을 폴더가 아닌 의미와 관계를 중심으로 탐색하는 시스템**

File Network는 기존 파일 시스템의 계층적인 폴더 구조를 유지하면서, 파일 간 의미적 관계를 연결해 새로운 방식의 파일 탐색 경험을 제공하는 프로젝트입니다.

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
| 접근 불가 파일 | 파일명 및 메타데이터 분석 가능한 범위에서 처리 |

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
├── app/
│   ├── __init__.py
│   └── main.py
├── core/
│   ├── __init__.py
│   └── scanner/
│       ├── __init__.py
│       └── scanner.py
├── database/
│   ├── __init__.py
│   └── db.py
├── requirements.txt
├── README.md
└── .gitignore
```

## 5. 실행 방법

### 1) 저장소 복제

```bash
git clone https://github.com/사용자명/file-network.git
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

테스트할 디렉터리를 지정합니다.

```bash
mkdir -p ~/file-network-test
echo "Hello File Network" > ~/file-network-test/test.txt
```

```bash
python -m app.main "$HOME/file-network-test"
```

실행 후 `database/file_network.db`에 파일 메타데이터가 저장됩니다.

## 6. 현재 구현 상태

- [x] Python 프로젝트 초기 구조 구성
- [x] SQLite 데이터베이스 초기화
- [x] 디렉터리 재귀 탐색
- [x] 파일 메타데이터 수집
- [x] 파일 유형 분류
- [x] 배치 단위 데이터베이스 저장
- [x] 동일 경로 파일 정보 갱신

## 7. 향후 개발 계획

### Phase 1. 파일 스캔 안정화
- [ ] 파일 이동 및 삭제 감지
- [ ] 파일 고유 식별자 도입
- [ ] 변경된 파일만 재분석
- [ ] 스캔 진행률 및 취소 기능
- [ ] macOS 및 Windows 호환성 개선

### Phase 2. 파일 내용 분석
- [ ] PDF, DOCX, PPTX, XLSX 파서 구현
- [ ] 소스 코드 분석
- [ ] 파일별 키워드 및 주제 추출
- [ ] 파일 분석 상태 관리

### Phase 3. 관계 분석 및 검색
- [ ] 파일 간 관계 데이터 모델링
- [ ] 관계 유형 및 신뢰도 저장
- [ ] SQLite FTS5 검색
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

## 8. 개발 원칙

1. 기존 파일 시스템과 원본 파일을 변경하지 않습니다.
2. 모든 파일을 동일한 수준으로 분석하지 않습니다.
3. 파일 분석 결과와 관계의 근거를 추적할 수 있도록 설계합니다.
4. 의미적 유사성과 명시적인 의존 관계를 구분합니다.
5. 초기에는 Python으로 기능을 검증하고, 성능 병목이 확인되면 필요한 부분을 최적화합니다.

---

**File Network**  
*From file paths to meaningful connections.*
