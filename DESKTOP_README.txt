File Network — Windows x64 개발용 실행 배포본

1. 받은 ZIP의 모든 파일을 한 폴더에 압축 해제합니다.
2. FileNetwork.exe를 실행합니다. _internal 폴더도 반드시 함께 있어야 합니다.
3. 폴더를 선택하고 스캔·분석한 뒤 본문/파일명/통합 검색을 사용합니다.
4. CP949 한글 텍스트 폴더는 인코딩을 CP949로 선택합니다.

Python을 별도로 설치할 필요가 없습니다. 현재 코드 서명 없는 개발용 배포본입니다.
Windows/조직 정책에 따라 실행이 제한될 수 있으며 코드 서명·설치 마법사·자동 업데이트는 아직 제공하지 않습니다.

배포 실행 DB: %LOCALAPPDATA%\file-network\file_network.db
화면 설정: OS 표준 설정 폴더\file-network\gui-settings.json
소스 실행의 기존 database/file_network.db는 자동 이동/덮어쓰기 하지 않습니다.
처음 배포본을 쓰는 경우 폴더를 다시 스캔·분석합니다.
업데이트는 앱을 닫고 새 ZIP을 별도 폴더에 풀어 실행하면 사용자 데이터가 유지됩니다.
원본 파일을 편집하거나 외부 AI API로 보내지 않습니다.

문제 제보 및 최신 실행 결과:
https://github.com/wjddydghks/file-network

포함 라이브러리의 메타데이터/라이선스는 _internal의 각 *.dist-info 폴더에 보관합니다.
프로젝트 소스와 의존성 목록은 위 GitHub 저장소에서 확인할 수 있습니다.
