# 목표 루프 검증 기록

검증 환경: macOS, Python 3.12 서버, Node 22, ACP Python/TypeScript SDK,
Playwright Chromium. 외부 모델은 FakeProvider로 대체했고 파일·SQLite·셸 명령·
ACP stdio·클라이언트 컨트롤러·브라우저 화면은 실제 구현을 실행했다.

## 관찰한 동작

| 시나리오 | 결과 | 근거 |
|---|---|---|
| 메뉴에서 목표 시작 | 통과 | `packages/vscode/scripts/test-goal-loop.mts`의 실제 브라우저→컨트롤러→Python 경로 |
| 첫 검증 실패 후 파일 수정·재검증 | 통과 | wrong 파일을 42로 수정하고 실제 검증 프로세스 exit 0 확인 |
| 상태 조회 중 기존 작업 유지 | 통과 | ACP E2E, AgentSession 이벤트 테스트, Webview 컨트롤러·브라우저 |
| 실행 중 취소 | 통과 | ACP E2E의 실제 긴 프로세스 취소 및 cancelled 응답 |
| 서버 재시작 후 재개 | 통과 | 동일 DB를 다른 서버 프로세스로 열어 저장된 목표 재개·검증 통과 |
| 반복·Step·시간·동일 실패 한도 | 통과 | `server/tests/test_goal.py`, 한도 소진은 verification failed |
| 부모·자식 공유 예산·권한 거부 | 통과 | 하위 세션의 소비·거부가 부모 목표의 성공을 막음 |
| 미신뢰 프로젝트·설정 변경 | 통과 | 준비 거부·변경된 검증 명령 미실행 |
| 비밀 환경변수 제거 | 통과 | 실제 검증 프로세스에서 PERDIX_SECRET_TEST 부재 확인 |
| 검증 allow_always 복구 | 통과 | 이벤트 로그로 정책 복구, 해당 명령만 허용 |
| 헤드리스 실패 종료 코드 | 통과 | 빌드한 CLI 프로세스에서 verification failed와 exit 1 확인 |
| 키보드·연결·실행 중 메뉴 | 통과 | 방향키·Enter·Escape, 실행 중 status/cancel만 노출 |
| 밝은·어두운 테마·좁은 화면 | 통과 | 390/1440px 두 테마 및 260px에서 수평 넘침 없음 |

## 실행한 검사

- `pnpm -r build`: 성공. 기존 CJS `import.meta` 경고는 남아 있다.
- `pnpm -r test`: protocol 2, CLI 132, VS Code 16 통과. 추가 헤드리스 테스트를
  포함한 최종 CLI 재실행은 133개 통과했다.
- `pnpm -r lint`, `pnpm -r typecheck`: 통과. 새 브라우저 검사 스크립트도 타입 검사에 포함.
- `pnpm --filter perdix-vscode test:webview`, `test:loop`: 통과.
- `uv run pytest tests/test_goal.py tests/test_acp_e2e.py -q`: 19개 통과.
- `uv run ruff check .`, `uv run mypy src`, `git diff --check`: 통과.
- Python 전체 테스트는 276개 통과, 1개 실패다.
  `test_missing_api_key_is_clear_provider_error`가 실패한다.
  해당 테스트는 환경변수 두 개만 지우지만 설치된 Anthropic SDK가 로컬 인증 프로필을
  자동 발견해 실제 API의 빈 messages 오류를 받는다. 공급자와 해당 테스트 파일은
  HEAD와 동일함을 `git diff --exit-code`로 확인했다. 이 기능의 변경으로 고치거나
  skip 처리하지 않았다.

## 화면과 실행 산출물

`pnpm --filter perdix-vscode test:loop`가 Git에서 제외된 `dist/goal-loop-qa/`에
이벤트 JSON과 PNG를 생성한다. 직접 열어 메뉴, 권한 대기 중 메뉴, 검증 통과,
밝은·어두운 테마, 좁은 사이드바를 확인했다. 긴 프로젝트 경로가 상태 표시줄의
수평 넘침을 만들던 부분은 줄바꿈으로 해결했다. 기존 VS Code 토큰과 CLI InputBox를
재사용하고 HTML 삽입이나 이미지로 만든 메뉴를 사용하지 않는다.

실제 LLM의 과업 수행 품질, 설치된 VS Code 호스트, Windows 설치 프로그램은
이번 검증 범위가 아니다. 검증 명령이 목표를 충분히 판별하는지는 프로젝트별 책임이다.
