# File Network

**From file paths to meaningful connections.**

파일을 폴더 경로가 아닌 내용과 의미, 관계를 중심으로 탐색하는 로컬 기반 파일 관리·분석 시스템입니다.

File Network는 PC에 저장된 파일을 스캔하고 메타데이터를 로컬 데이터베이스에 관리합니다. 향후 문서 내용 검색과 파일 간 관계 분석, 생성형 AI 기반 질의응답 및 비교 기능으로 확장하는 것을 목표로 합니다.

---

## 1. 프로젝트 소개

기존 파일 시스템에서는 사용자가 파일의 저장 위치나 폴더 경로를 기억해야 원하는 파일을 찾기 쉽습니다.

File Network는 파일의 위치뿐 아니라 메타데이터와 문서 내용, 파일 간 연관성을 활용해 파일을 탐색하는 환경을 만드는 프로젝트입니다.

핵심 설계 원칙은 **파일 관리와 데이터 저장은 로컬 엔진이 담당하고, 생성형 AI는 사용자가 허용한 데이터에 대해서만 분석을 수행한다**는 것입니다.

### 주요 목표

- PC 내 파일 메타데이터 통합 관리
- 파일 생성·수정·삭제·이동 감지
- 키워드 및 의미 기반 파일 검색
- 문서 내용 분석 및 파일 간 관계 발견
- 생성형 AI를 활용한 문서 요약, 비교, 질의응답
- 파일 간 관계를 시각적으로 탐색

## 2. 시스템 구성 및 역할

File Network는 로컬 엔진을 중심으로 동작하며, 생성형 AI는 선택적으로 연결하는 분석 모듈로 설계합니다.

| 구성 요소 | 역할 |
|---|---|
| File Scanner | 디렉터리 재귀 탐색 및 파일 메타데이터 수집 |
| Incremental Scan | 기존 DB 정보와 비교해 신규·수정·변경 없는 파일 구분 |
| Change Tracker | 파일 삭제 및 이동 추적 |
| Local Database | 파일 메타데이터, 스캔 상태 및 분석 결과 저장 |
| Search Engine | 키워드 및 향후 의미 기반 검색 |
| AI Service | 허용된 문서 데이터의 요약·비교·질의응답 |
| UI | 검색 결과, 파일 정보 및 AI 분석 결과 제공 |

### 데이터 처리 흐름

```text
사용자 요청
    |
    v
로컬 엔진
    |
    +--> 파일 검색 및 DB 조회
    |
    +--> 필요한 문서 내용 추출
    |
    +--> 민감정보 검사 및 전송 대상 선별
                 |
                 v
        사용자 전송 승인
                 |
                 v
        외부 생성형 AI API
                 |
                 v
          분석 결과 반환
                 |
                 v
       로컬 엔진에서 결과 검증
                 |
                 v
          사용자에게 제공
```

AI가 파일 시스템이나 SQLite에 직접 접근하지 않습니다. AI는 로컬 엔진이 전달한 제한된 데이터만 분석하며, DB의 실제 변경은 로컬 엔진이 검증한 뒤 수행하도록 설계합니다.

## 3. AI 활용 및 보안 정책

생성형 AI를 활용하되 로컬 파일의 보안과 사용자의 데이터 통제권을 우선합니다.

### 3.1 AI 활용 범위

향후 생성형 AI를 통해 다음 기능을 제공하는 것을 목표로 합니다.

- **문서 질의응답:** 사용자가 선택하거나 검색한 문서를 근거로 질문에 답변
- **문서 요약:** 긴 문서의 핵심 내용과 주요 항목 정리
- **문서 비교:** 여러 문서의 공통점, 차이점 및 변경 사항 분석
- **관계 분석:** 프로젝트·주제·키워드 등을 바탕으로 관련 파일 탐색 지원
- **자연어 검색:** 일상적인 문장으로 파일을 검색할 수 있도록 지원

### 3.2 로컬 우선 처리 원칙

- 파일 원본은 사용자의 로컬 파일 시스템에 유지합니다.
- 파일 메타데이터와 검색 데이터베이스는 로컬에서 관리합니다.
- 파일 스캔, DB 조회, 검색 대상 선정은 로컬 엔진에서 수행합니다.
- 외부 AI API는 사용자가 AI 분석을 요청한 경우에만 사용합니다.
- AI가 DB에 직접 접속하거나 파일을 임의로 수정하지 못하도록 역할을 분리합니다.

### 3.3 외부 API 데이터 전송 정책

외부 생성형 AI API를 사용할 때는 다음 정책을 적용하는 것을 목표로 합니다.

1. **최소 데이터 전송:** 분석에 필요한 문서 일부와 문맥만 선별해 전송합니다. 원본 파일 전체를 기본적으로 전송하지 않습니다.
2. **사용자 승인:** 외부 전송 전에 대상 파일과 전송할 내용을 확인하고 승인할 수 있도록 합니다.
3. **민감정보 보호:** 개인정보, 인증정보, 기밀정보 등이 포함된 데이터는 탐지 후 기본적으로 전송을 차단하거나 로컬 분석만 허용합니다.
4. **비식별화:** 분석에 불필요한 이름, 연락처, 식별 정보 등은 가능한 범위에서 마스킹하거나 대체합니다. 단, 마스킹만으로 재식별 위험이 완전히 사라지는 것은 아닙니다.
5. **전송 범위 통제:** 사용자가 선택한 파일과 검색 결과 중 필요한 데이터만 전송합니다.
6. **API 키 보호:** API 키를 소스 코드나 저장소에 포함하지 않고 환경변수 또는 안전한 비밀정보 관리 방식을 사용합니다.
7. **전송 기록 관리:** 전송 여부와 대상 등 필요한 기록을 남기되, 민감한 원문이 로그에 불필요하게 저장되지 않도록 합니다.
8. **제공 업체 정책 확인:** API 제공 업체의 데이터 보관, 학습 사용 여부, 삭제 및 처리 정책을 확인한 뒤 사용합니다.

### 3.4 로컬 전용 모드

외부 API 사용을 원하지 않는 사용자를 위해 외부 전송을 비활성화할 수 있는 로컬 전용 모드를 지향합니다.

- 로컬 파일 스캔 및 DB 관리는 계속 사용할 수 있어야 합니다.
- 로컬에서 가능한 검색과 분석 기능은 외부 API 없이 동작하도록 설계합니다.
- 외부 AI 분석 기능은 비활성화하거나 사용할 수 없음을 명확히 표시합니다.

> **중요:** 외부 API로 데이터를 전송하는 순간 전송된 데이터에 대한 제3자 처리 위험이 발생할 수 있습니다. 일부 데이터만 보내거나 마스킹하더라도 보안이 완전히 보장되는 것은 아닙니다. 민감하거나 기밀인 문서는 외부 전송을 차단하는 것을 기본 원칙으로 합니다.

## 4. 파일 분석 정책

모든 파일을 네트워크에 등록하되, 파일 유형과 접근 가능 여부에 따라 분석 수준을 구분합니다.

| 파일 유형 | 분석 방향 |
|---|---|
| TXT, MD, CSV 등 텍스트 | 메타데이터 및 텍스트 내용 분석 |
| PDF, DOCX, PPTX, XLSX | 메타데이터 및 문서 내용 추출 |
| 소스 코드 | 메타데이터 및 코드 내용 분석 |
| 이미지·동영상·음성 | 우선 메타데이터 중심으로 관리 |
| 압축 파일 | 우선 파일명 및 메타데이터 중심으로 관리 |
| 실행 파일 및 바이너리 | 파일명 및 메타데이터 중심으로 관리 |
| 접근 불가 파일 | 접근 가능한 정보만 수집하고 오류 상태 기록 |

내용을 추출할 수 없는 파일도 파일 메타데이터를 관리할 수 있는 범위에서 네트워크에 포함하는 것을 목표로 합니다.

## 5. 기술 스택

| 기술 | 용도 | 상태 |
|---|---|---|
| Python | 파일 스캔 및 핵심 엔진 | 사용 중 |
| SQLite | 로컬 파일 메타데이터 저장 | 사용 중 |
| pytest | 테스트 | 사용 중 |
| SQLite FTS5 | 전문 검색 | 도입 예정 |
| PyMuPDF | PDF 텍스트 추출 | 도입 예정 |
| python-docx | DOCX 텍스트 추출 | 도입 예정 |
| python-pptx | PPTX 텍스트 추출 | 도입 예정 |
| openpyxl | XLSX 데이터 추출 | 도입 예정 |
| Embedding API 또는 로컬 임베딩 모델 | 의미 벡터 생성 | 검토 예정 |
| FAISS | 벡터 검색 | 검토 예정 |
| 외부 생성형 AI API | 문서 요약·비교·질의응답 | 설계 단계 |
| PySide6 | 데스크톱 UI | 도입 예정 |
| Cytoscape.js | 파일 관계 시각화 | 도입 예정 |

현재 `requirements.txt`에 명시된 기본 개발 의존성은 `pytest`입니다. 향후 기능을 추가할 때 실제 사용하는 라이브러리를 의존성 목록에 반영합니다.

## 6. 프로젝트 구조

```text
file-network/
├── .github/
│   └── workflows/
│       └── ci.yml
├── app/
│   ├── __init__.py
│   ├── main.py
│   └── incremental_scan.py
├── core/
│   └── scanner/
│       └── scanner.py
├── database/
│   ├── db.py
│   └── scan_sessions.py
├── tests/
├── requirements.txt
├── README.md
└── .gitignore
```

실제 브랜치 및 개발 단계에 따라 파일 구성은 달라질 수 있습니다.

## 7. 실행 방법

### 7.1 사전 요구 사항

- Python 3.9 이상 권장
- Git
- Windows 명령 프롬프트(CMD), PowerShell 또는 macOS/Linux 터미널

### 7.2 저장소 복제

```bash
git clone https://github.com/wjddydghks/file-network.git
cd file-network
```

### 7.3 가상환경 생성

**Windows CMD**

```cmd
py -m venv .venv
```

**Windows PowerShell**

```powershell
py -m venv .venv
```

**macOS / Linux**

```bash
python3 -m venv .venv
```

### 7.4 가상환경 활성화

**Windows CMD**

```cmd
.venv\Scripts\activate
```

**Windows PowerShell**

```powershell
.\.venv\Scripts\Activate.ps1
```

PowerShell에서 실행 정책 오류가 발생하면 현재 사용자 범위에서 스크립트 실행 정책을 확인해야 합니다. CMD를 사용하면 위의 PowerShell 실행 정책 문제를 피할 수 있습니다.

**macOS / Linux**

```bash
source .venv/bin/activate
```

정상적으로 활성화되면 터미널 프롬프트 앞에 `(.venv)`가 표시됩니다.

### 7.5 패키지 설치

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 7.6 테스트용 디렉터리 생성

프로젝트 폴더와 스캔 대상 폴더는 분리하는 것을 권장합니다.

**Windows CMD**

```cmd
mkdir "%USERPROFILE%\file-network-test"
echo Hello File Network > "%USERPROFILE%\file-network-test\test.txt"
```

**Windows PowerShell**

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\file-network-test"
Set-Content "$env:USERPROFILE\file-network-test\test.txt" "Hello File Network"
```

**macOS / Linux**

```bash
mkdir -p "$HOME/file-network-test"
echo "Hello File Network" > "$HOME/file-network-test/test.txt"
```

### 7.7 프로그램 실행

명령어는 반드시 **프로젝트 루트(`file-network`) 디렉터리에서** 실행합니다.

**Windows CMD**

```cmd
python -m app.main "%USERPROFILE%\file-network-test"
```

**Windows PowerShell**

```powershell
python -m app.main "$env:USERPROFILE\file-network-test"
```

**macOS / Linux**

```bash
python -m app.main "$HOME/file-network-test"
```

실행 시 지정한 디렉터리를 스캔하고 파일 메타데이터를 로컬 SQLite 데이터베이스에 저장합니다.

기본 데이터베이스 위치:

```text
database/file_network.db
```

### 7.8 실행 시 주의 사항

- `python -m app.main`은 프로젝트 루트에서 실행해야 합니다.
- 스캔 대상 경로는 실제로 존재하는 디렉터리여야 합니다.
- Windows CMD에서는 Linux의 `~` 및 `$HOME` 문법을 사용하지 않습니다.
- Windows CMD에서 사용자 홈 경로는 `%USERPROFILE%`로 지정합니다.
- 프로젝트 코드 폴더 전체를 무심코 스캔하지 말고 별도의 테스트 디렉터리로 먼저 검증하는 것을 권장합니다.
- Windows 등의 파일 시스템에서 파일 식별자가 SQLite 정수 범위를 넘는 경우에도 정밀도를 보존해 저장합니다. 범위 초과 값은 `int:` 접두사의 텍스트로 저장하고 앱의 DB 연결에서 정수로 복원합니다. 기존 DB 삭제나 재생성은 필요하지 않습니다.

## 8. 개발 현황

아래 항목은 현재까지 진행된 구현을 기준으로 정리합니다. 문서 추출, 의미 검색, 생성형 AI 연동 등 아직 구현되지 않은 기능은 완료 항목으로 표시하지 않습니다.

### 구현된 기능

- [x] Python 프로젝트 기본 구조
- [x] SQLite 데이터베이스 초기화 및 파일 메타데이터 저장
- [x] 디렉터리 재귀 탐색
- [x] 파일 메타데이터 수집 및 유형 분류
- [x] 배치 단위 DB 저장
- [x] 기존 파일 정보와 비교하는 증분 스캔
- [x] 스캔 세션 및 실행 결과 기록
- [x] 파일 삭제 감지 및 소프트 삭제 처리
- [x] 파일 이동 감지 및 기존 레코드 갱신
- [x] 접근 오류가 있는 불완전한 스캔에서 오삭제를 방지하는 처리

### 향후 개발 항목

- [ ] 문서 형식별 텍스트 추출기 구현
- [ ] 파일 내용 및 청크 데이터 관리
- [ ] SQLite FTS5 기반 전문 검색
- [ ] 임베딩 기반 의미 검색
- [ ] 생성형 AI API 연동
- [ ] 문서 요약 및 질의응답
- [ ] 여러 문서 간 비교 및 관계 분석
- [ ] 외부 API 전송 승인 및 민감정보 보호 기능
- [ ] 로컬 전용 모드
- [ ] PySide6 데스크톱 UI
- [ ] 파일 관계 그래프 시각화
- [ ] Windows/macOS 환경별 자동 테스트 확대

## 9. Git 협업 규칙

프로젝트 변경 사항은 Issue를 기반으로 관리하며, 기능 개발과 수정은 별도 브랜치에서 진행합니다.

### 9.1 Issue

제목 형식:

```text
[Type] 작업 내용
```

| Type | 설명 |
|---|---|
| Feature | 신규 기능 |
| Bug | 버그 수정 |
| Refactor | 코드 구조 개선 |
| Test | 테스트 작성 및 개선 |
| CI | CI/CD 및 자동화 |
| Docs | 문서 수정 |
| Chore | 기타 설정 및 유지보수 |

Issue에는 작업 배경, 목표, 세부 작업, 완료 조건을 포함합니다.

### 9.2 Branch

브랜치 이름 형식:

```text
type/issue-number-description
```

예시:

```text
feature/12-content-analyzer
fix/15-symlink-scan
test/18-scanner-test
docs/20-readme-update
```

- `main` 브랜치에 직접 작업하지 않습니다.
- Issue에 연결된 별도 브랜치에서 작업합니다.

### 9.3 Commit

커밋 메시지 형식:

```text
type: 작업 내용
```

예시:

```text
feat: add document content extraction
fix: handle inaccessible directories
refactor: separate scanner logic
test: add incremental scan tests
docs: update readme
```

### 9.4 Pull Request

모든 변경 사항은 Pull Request를 통해 `main`에 반영합니다.

PR에는 관련 Issue, 주요 변경 사항, 테스트 결과 및 확인 사항을 포함합니다. 관련 Issue는 `Closes #이슈번호`로 연결합니다.

## 10. 개발 원칙

1. 원본 파일을 임의로 수정하거나 삭제하지 않습니다.
2. 파일 메타데이터와 분석 데이터는 로컬에서 관리합니다.
3. 파일 스캔과 DB 관리 기능은 생성형 AI API에 의존하지 않습니다.
4. AI에는 사용자가 허용한 최소한의 데이터만 전달합니다.
5. 민감정보와 기밀 데이터는 외부 전송을 기본적으로 제한합니다.
6. AI 분석 결과를 DB에 반영할 때는 로컬 엔진에서 검증합니다.
7. 파일 간 관계와 AI 분석 결과의 근거를 추적할 수 있도록 설계합니다.
8. 기능을 추가할 때 테스트와 문서를 함께 갱신합니다.

---

**File Network**  
*From file paths to meaningful connections.*
