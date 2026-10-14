# 여러 터미널에서 팀으로 작업하기

각 터미널은 독립된 에이전트와 대화 기록을 갖는다. Python 서버들이 공용 SQLite
메시지함과 작업 목록을 사용한다. 팀 이름과 프로젝트 경로가 모두 같아야 연결된다.
먼저 리더 터미널을 시작한 뒤 구성원 터미널을 시작한다.

개발 소스에서 다음 명령을 실행한다. 설치된 CLI에는 새 빌드가 필요하다.

```bash
# 터미널 1: 리더, 대화형
pnpm --filter perdix dev --team demo --member lead --team-role leader

# 터미널 2: 구현 담당, 메시지가 오면 자동 실행
pnpm --filter perdix dev --team demo --member implement --team-worker \
  --mode build --permission-mode accept-edits

# 터미널 3: 검토 담당, 파일 수정과 명령 실행 금지
pnpm --filter perdix dev --team demo --member review --team-worker \
  --mode plan --permission-mode deny
```

리더에게 다음처럼 요청한다.

> implement에게 로그인 오류 수정 작업을 login-fix라는 ID로 배정해줘.
> 담당 파일 범위는 server/src/perdix/llm이야. review에게는 읽기 전용 검토를 맡겨줘.
> 둘이 직접 질문과 결과를 주고받고, 작업 목록에 완료 또는 막힌 이유를 기록해줘.

`team` 도구의 명령은 다음과 같다.

| action | 필요한 입력 | 동작 |
|---|---|---|
| `status` | 없음 | 구성원과 작업 상태 조회 |
| `send` | `recipient`, `body` | 한 구성원에게 메시지 전송 |
| `broadcast` | `body` | 자신을 제외한 참여 구성원에게 전송 |
| `assign` | `task_id`, `recipient`, `body`, `paths` | 리더가 작업 배정 또는 재배정 |
| `complete` | `task_id`, `body` | 담당자가 완료 결과 기록, 리더에게 전달 |
| `block` | `task_id`, `body` | 담당자가 막힌 이유 기록, 리더에게 전달 |
| `remove` | `recipient` | 리더가 구성원 제거, 진행 작업을 blocked로 전환 |
| `stop` | 없음 | 리더가 팀 종료, 대기 중인 worker 종료 |

진행 중인 작업들의 `paths`가 파일 또는 상위 디렉터리로 겹치면 배정을 거부한다.
검토처럼 수정하지 않는 작업은 `paths: []`를 쓴다. 이 목록은 작업 계획의 충돌을
막으며 파일 시스템 접근을 강제 제한하지 않는다. 별도 worktree를 사용하면 각자의
`--cwd`를 지정하고 모든 터미널에 동일한 `--team-project /원본/프로젝트`를 지정한다.
소스 변경의 병합은 사용자가 확인해서 수행한다.

대화형 터미널은 다음 사용자 요청 또는 모델 호출에서 메시지를 확인한다.
`--team-worker` 터미널은 LLM을 호출하지 않고 메시지를 기다리다가 수신 시 실행한다.
리더도 `--team-role leader --team-worker -p "팀에 맡길 목표"`로 자동 수신을 사용할 수
있다. 구성원이 참여하면 리더에게 참여 메시지가 전달된다. 구성원이 참여한 뒤 배정을
하도록 목표에 명시한다. 시작 목표는 한 번만
실행하며 이후에는 새 메시지를 기다린다. 작업 도중 새 메시지는 다음 모델 호출에 들어간다.

두 TUI 모두 `/team`을 도움말과 자동완성에 표시한다. `/team`만 입력하면 실행 방법을
안내한다. 팀에 연결하려면 시작할 때 `--team`과 `--member`를 지정한다.
`/team status`로 상태를 보고 `/team stop`으로 종료한다. worker의 Ctrl+C는 표준 ACP
취소를 보내고 종료한다. 정상 종료 시 이름을 반환하고 진행 작업을 blocked로 바꾼다.
비정상 종료로 이름이 남아 있으면 리더에게 `remove`로 제거한 뒤 새 터미널을 시작하고
미완료 작업을 재배정하도록 요청한다. 재배정 전 이전 작업자가 멈췄는지 확인한다.
리더가 비정상 종료해 복구할 수 없다면 새 팀 이름으로 다시 시작한다.
종료된 팀은 보존하며, 다시 시작할 때는 새 팀 이름을 쓴다.

공유 DB 기본 위치는 `~/.perdix/teams.sqlite3`다. `--team-db`를 쓸 때는 모든 터미널에
같은 경로를 지정한다. 메시지와 이벤트를 보존하고 읽음 여부만 별도 기록한다.
수신 기록 후 확인하기 전에 중단되면 같은 메시지 ID가 다시 전달될 수 있다.
메시지함에는 구성원별 미확인 메시지를 최대 100개 저장한다.

팀 통신은 도구 권한을 넓히지 않는다. 기본 worker는 `deny`이며 코드 수정에는
`--permission-mode accept-edits`를 명시한다. 명령 실행은 기존 신뢰·권한 정책을 따른다.
에이전트가 완료를 기록했다는 사실과 테스트가 통과했다는 사실은 별개이므로 리더가
테스트 결과와 변경 내용을 확인해야 한다.

검증은 `pnpm -r build` 후 `cd server && uv run pytest`로 실행한다. 팀 테스트는
`server/team_tests/`에 있으며 실제 ACP 서버와 빌드한 CLI를 별도 프로세스로 구동한다.
