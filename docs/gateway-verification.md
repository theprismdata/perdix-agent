# 게이트웨이 검증 기록

검증일: 2026-10-12. 실제 외부 계정 연결과 로컬 구현 검증을 구분한다.

## 실행 결과

- `pnpm -r test`: 프로토콜 2개, VS Code 16개, CLI 143개 통과.
- `pnpm -r lint`, `pnpm -r typecheck`: 통과.
- `cd server && uv run pytest`: 315개 중 314개 통과, 1개 실패.
- 게이트웨이 신규 테스트 18개: 전부 통과.
- `uv run ruff check .`, `uv run mypy src`: 통과(61개 소스 파일).
- `uv build --wheel`: 통과. HTML/CSS/JS/favicon 정적 파일 포함 확인.
- `node --check server/src/perdix/gateway/static/dashboard.js`: 통과.

실패한 기존 테스트는
`tests/test_anthropic_adapter.py::test_missing_api_key_is_clear_provider_error`다.
환경 변수의 키를 제거해도 로컬 인증 정보가 자동 발견되어 Anthropic에 빈 메시지
요청이 전송되었고, 기대한 인증 설정 안내 대신 HTTP 400이 반환됐다. 이 작업에서는
해당 공급자 코드와 테스트를 수정하지 않았다.

## 실제 로컬 경로

브라우저에서 토큰 연결, 빈 작업 목록, 작업 접수, ACP 하위 프로세스의 파일 쓰기
승인, 결과 표시, 취소, GitHub 이슈 가져오기 오류를 확인했다. Telegram/GitHub
외부 HTTP 호출은 테스트에서 응답을 대체했고, 에이전트 실행은 실제 ACP 프로세스를
사용했다. 웹 네트워크 차단과 복구에서는 재연결 배지와 한국어 안내 전환을 확인했다.

390/1440/1920 CSS 픽셀의 밝은/어두운 화면을 156개 캡처했다. 긴 연속 문자열과 전체
페이지를 포함한 52개 상태 검사에서 가로 넘침이 없었다. 확대 검증은 1440 화면에
CSS 뷰포트 720×450, DPR 2를 적용한
200% 동등 레이아웃 검증이며, Chrome 자체 확대 메뉴 조작은 아니다.

로그인/작업 화면의 모바일/데스크톱 Lighthouse 점수는 각각 성능·접근성·권장사항·
SEO 모두 100이다. 상세 산출물은 `.omo/evidence/gateway/`에 있다.

## 남은 운영 설정

실제 Telegram 메시지 왕복, 실제 GitHub 비공개 이슈 조회·웹훅 전달·결과 댓글 게시와
실제 LLM 작업은 운영용 계정/토큰으로 검증하지 않았다. 허용 사용자·채팅 ID,
등록 저장소, 서버 인증 정보를 설정해야 한다. GitHub 결과 댓글은 기본 비활성화다.
설정과 실행 방법은 [gateway.md](gateway.md)를 따른다.
