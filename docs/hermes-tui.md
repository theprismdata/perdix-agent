# Hermes TUI 평가판

Hermes의 전체 화면 TUI를 Perdix 서버에 연결한다. 모델 호출·도구·권한·저장된 대화는
기존 Perdix Python 서버가 처리한다. 기본 TUI와 함께 비교할 수 있다.

저장소에서 의존성을 설치하고 빌드한 뒤 실제 터미널에서 실행한다.

```bash
pnpm install
pnpm -r build
node packages/cli/dist/main.js --tui hermes --cwd /작업/프로젝트
```

개발 실행은 `pnpm --filter perdix dev --tui hermes`이다.
기존 공급자 인증과 `--model`, `--mode`, `--server` 설정을 사용한다.
메인 모델은 요청 의도에 따라 `switch_mode`로 모드를 자동 전환한다. 질문·조사는
`chat`, 계획만 요청하면 `plan`, 구현·수정·실행은 `build`다. 전환은 현재 작업부터
반영되고 화면에도 전달된다. 쓰기·실행의 기존 권한 승인은 유지한다.
수동 모드는 시작 모드를 정하며, 다음 요청에서는 의도에 따라 다시 바뀔 수 있다.
`~/.perdix/agents.json`의 `agents.chat`, `agents.plan`, `agents.build`를 지정하면 모드 전환과
함께 모델·effort도 바뀐다. 사용자 설정 폴더의 파일을 자동으로 읽으며
다른 경로는 서버 옵션 `--agents-config`로 지정한다. 설정 변경 후 재시작한다.
`--resume <세션ID>`로 기존 대화를 복원할 수 있고, `--list`로 ID를 확인한다.
`-p`와 파이프 입력은 기본 CLI를 사용한다.

화면에서 한글 입력, 응답 스트리밍, 도구 결과, 권한 승인, 취소와 세션 선택을
평가한다. `/new`, `/resume`, `/model`, `/mode`, `/theme`, `/help`, `/quit`를 제공한다.
권한 창의 기본 선택은 거부이며 서버가 제공한 승인 범위만 선택할 수 있다.
`HERMES_TUI_THEME=light` 또는 `dark`로 시작 테마를 지정할 수 있다.

호환되는 명령을 우선 연결했다. `/help`와 `/` 자동완성은 연결된 명령을 안내한다.

| 명령 | 연결 동작 |
|---|---|
| `/login [openai\|anthropic]` | 공급자 선택 후 기존 브라우저 인증 실행; 완료 후 서버 재연결 |
| `/logout [openai\|anthropic]` | 기존 인증 해제 후 서버 재연결 |
| `/auth status [openai\|anthropic]` | 현재 인증 상태 표시 |
| `/agent`, `/mode` | 현재 모드 조회; `chat`, `plan`, `build`로 변경 |
| `/model` | 서버가 제공한 모델 선택; 현재 세션에 적용 |
| `/new`, `/clear` | 확인 후 새 대화 시작; 기존 기록 보존 |
| `/sessions`, `/resume [ID]` | 저장된 세션 선택·복원 |
| `/status` | 실제 세션·모델·모드·작업 상태 표시 |
| `/usage` | ACP가 보고한 컨텍스트 사용량·비용; 없는 통계는 미제공으로 안내 |
| `/cancel`, `/stop` | Perdix 작업 취소 요청 |
| `/memories` | 메모리 상태·`use on/off`, `generate on/off`를 서버에 전달 |
| `/loop [목표]`, `/loop status`, `/loop cancel` | 목표 루프 요청·조회·취소; 실행에는 기존 프로젝트 신뢰·검증 설정 필요 |
| `/save` | 자동 저장·세션 복원 안내; 별도 파일 내보내기는 하지 않음 |
| `/theme [auto\|light\|dark]` | 현재 실행의 테마 조회·변경 |
| `/copy`, `/redraw`, `/quit`, `/exit` | Hermes의 응답 복사·재그리기·종료 |

`/login`은 공급자 선택 화면을 연다. 인증 중에는 TUI를 잠시 내려 터미널과 브라우저에서
기존 인증 절차를 진행하고, 완료·취소하면 대화 화면으로 돌아온다. 진행 중인 작업이
있으면 인증 변경을 거부한다. 로그인·로그아웃 후 저장된 세션을 다시 불러오며 모델은
자동 변경하지 않는다. 필요하면 `/model`로 변경한다. 채팅 입력창에 API 키를 입력하지 않는다.
환경변수 키가 설정되어 있으면 로그아웃 후에도 해당 키가 우선 사용될 수 있다.
외부 터미널의 `perdix auth login --provider openai`도 사용할 수 있다.
Hermes의 `/yolo`, `/undo`, `/compress`, `/branch`처럼 실행 권한이나 기록 의미가
다른 기능은 이번 호환 목록에 넣지 않는다.

Hermes 원본의 영어 문구와 메뉴 일부는 남아 있다. 음성·결제·Hermes 플러그인·
상세 설정은 연결하지 않았으며 기능 플래그로 비활성화한다.
플래그 정본은 `packages/hermes-tui/vendor/ui-tui/src/perdixFeatures.ts`의
`PERDIX_FEATURES`다. 미연결 기능은 `false`이며 메뉴·단축키와 요청 전송을 제한한다.
기능을 서버에 연결하고 검증한 뒤 해당 플래그를 `true`로 바꾸고 TUI를 다시 빌드한다.
Perdix 실행기가 설정하는 `HERMES_TUI_PERDIX=1`에서만 적용되며 원본 구현은 보존한다.
Perdix 목표 루프·메모리의 전용 화면과 IDE bridge 표시는 아직 평가 범위에 포함하지 않는다.
배포 설치 파일에는 아직 동봉하지 않았다.

원본 버전과 MIT 라이선스는 [UPSTREAM.md](../packages/hermes-tui/UPSTREAM.md),
연결 구조는 [ADR 0015](adr/0015-hermes-tui-evaluation.md)에 기록한다.
평가 후 기본 화면 교체 여부를 결정한다.
현재 검증 결과와 화면의 남은 문제는 [평가 기록](hermes-tui-evaluation.md)을 참고한다.
