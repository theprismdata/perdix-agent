# ADR 0012: 로컬 상주 게이트웨이와 채널 연동

상태: 채택

## 목적

Telegram, GitHub, 웹 대시보드에서 작업을 접수하고 화면 연결과 무관하게
Python ACP 서버가 작업을 계속 수행하도록 한다.

## 결정

Python `perdix-gateway`가 HTTP 대시보드와 채널 어댑터를 제공한다. 프로젝트는
운영자가 설정 파일에 등록한 이름과 절대 경로로만 선택한다. 각 작업은 독립 ACP
stdio 서버와 세션을 사용하며, 웹 연결이 끊겨도 서버는 작업 종료까지 유지된다.
LLM·도구·정책은 기존 서버에 남는다. 게이트웨이는 표준 ACP initialize,
session/new, session/prompt, session/cancel, session/request_permission,
session/update만 사용한다. HTTP API는 ACP를 대체하지 않는 채널 제어 API다.

게이트웨이 기록은 별도 SQLite append-only 이벤트 테이블이다. 상태는 이벤트에서
투영한다. 재시작 시 미완료 작업은 interrupted로 기록하며 자동 재실행하지 않는다.
외부 전달 ID는 고유 키로 저장해 중복 작업을 방지한다.

기본 bind는 loopback이며 대시보드는 Bearer 토큰으로 인증한다. Telegram은 long
polling과 사용자·chat allowlist를 사용한다. GitHub Webhook은 원문 HMAC 서명,
저장소·발신자 allowlist, 명시적인 `/perdix` 이슈 댓글로만 접수한다. GitHub
완료 댓글은 별도 opt-in 설정이다. 권한 승인은 인증한 웹 대시보드에서만 받는다.
Telegram이나 GitHub 입력이 서버 실행 정책 또는 프로젝트 신뢰를 완화하지 못한다.

ACP 자식 프로세스 env는 기존 scrub_env 계약을 사용한다. 공급자 키는 기존 로컬
credentials 파일로 받으며 게이트웨이 채널 비밀은 자식에게 전달하지 않는다.

## 범위

1차는 작업 제출·목록·기록·취소·권한 승인, Telegram 요청·결과 알림,
GitHub 이슈 수동 가져오기와 서명 Webhook, 반응형 대시보드다.
기존 CLI/IDE의 게이트웨이 접속 전환, 다중 사용자 호스팅, 원격 노드 실행은
이번 범위에 포함하지 않는다.
