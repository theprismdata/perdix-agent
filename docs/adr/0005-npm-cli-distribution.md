# ADR 0005: npm CLI 패키지 배포

상태: 폐기 — ADR 0007의 Windows 설치 프로그램으로 대체. npm 배포용 스크립트와 패키지는 제거했다.

## 배경

Windows를 포함한 환경에서 저장소 체크아웃 없이 `npm install -g`로 CLI를 설치해야 한다.
에이전트 로직은 Python에 유지한다.

## 결정

- `perdix` npm 패키지에 CLI 번들과 Python 서버 소스, pyproject.toml, uv.lock을 포함한다.
- 내부 protocol 패키지는 CLI에 번들링하고 외부 런타임 의존성은 npm으로 설치한다.
- `prepack`에서 빌드와 서버 복사를 수행한다. 설치 시 임의의 postinstall 스크립트는 실행하지 않는다.
- 명시적 서버 지정과 PATH 검색 다음으로 동봉 서버를 사용한다. uv가 잠금 파일에 따라
  Python 환경을 준비하며, 최초 서버 실행 시 다운로드가 필요할 수 있다.
- Node.js 22 이상과 uv는 사전 요구사항이다. npm 레지스트리 공개는 별도 배포 작업이다.

## 영향

Windows의 명령 래퍼는 npm이 생성한다. 설치 디렉터리는 서버 가상환경을 생성할 수 있어야 한다.
소스 저장소용 서버 탐색은 개발 환경을 위해 유지한다.
