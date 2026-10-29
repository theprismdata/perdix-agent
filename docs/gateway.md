# Telegram · GitHub · 웹 게이트웨이

`perdix-gateway`는 상주하는 Python 프로세스다. 등록한 프로젝트의 작업을 독립 ACP
서버에 전달하고 브라우저가 닫혀도 작업을 유지한다. 기존 CLI와 VS Code 연결 방식은
그대로이며 아직 이 게이트웨이에 직접 접속하지 않는다.

## 실행

```bash
cd server
uv sync
uv run perdix-gateway --config /절대/경로/gateway.json
```

설정 예시(경로·저장소·사용자 ID를 자신의 값으로 변경):

```json
{
  "host": "127.0.0.1",
  "port": 18790,
  "projects": {
    "my-app": {
      "cwd": "/absolute/path/to/my-app",
      "trust_project": false,
      "github_repository": "owner/repository"
    }
  },
  "server": {
    "provider": "openai",
    "model": "gpt-6.1-sol",
    "permission_mode": "interactive"
  },
  "telegram": {
    "allowed_user_ids": [123456789],
    "allowed_chat_ids": [123456789]
  },
  "github": {
    "allowed_senders": ["your-github-login"],
    "post_results": false
  }
}
```

`telegram`/`github` 항목을 생략하면 해당 채널은 비활성화된다. 프로젝트 경로는
존재하는 절대 경로여야 한다. `trust_project: true`는 프로젝트 MCP와 verify 명령을
신뢰한다는 운영자 결정이다. 채널 요청으로 이 값을 변경할 수 없다.

기동 전에 환경 변수에 다음 값을 설정한다. 대시보드 토큰은 `python -c
'import secrets; print(secrets.token_urlsafe(32))'`로 생성할 수 있다. 실제 토큰은
저장소·URL·채팅에 넣지 않는다.

| 환경 변수 | 용도 | 필요 조건 |
|---|---|---|
| `PERDIX_GATEWAY_TOKEN` | 대시보드 Bearer 인증, 24자 이상 | `auth: "token"`일 때만(루프백이 아닌 host) |
| `PERDIX_TELEGRAM_TOKEN` | BotFather에서 만든 봇 토큰 | Telegram 활성화 |
| `PERDIX_GITHUB_TOKEN` | GitHub REST 접근 | 비공개 저장소 읽기·결과 댓글 |
| `PERDIX_GITHUB_WEBHOOK_SECRET` | GitHub Webhook HMAC 검증 | Webhook 접수 |

채널 비밀은 게이트웨이 메모리에서만 사용하며 ACP 자식 env에는 넘기지 않는다.
모델 키는 기존 `~/.perdix/credentials.json` 또는 `server.credentials_path`의 파일을
사용한다. 게이트웨이에서는 자식 env를 정리하므로 공급자 API 키 env 전달을 사용하지
않는다. 기존 OpenAI OAuth 자격 증명 파일도 서버에서 사용할 수 있다.

`auth`를 생략하면 개인용 기본값으로 `host`가 `127.0.0.1`, `localhost`, `::1`일 때 `"none"`(연결
토큰 없음), 그 밖의 host는 `"token"`이다. `"none"`은 루프백에서만 허용하며 `PERDIX_GATEWAY_TOKEN`도
필요 없다. 루프백에서 토큰을 쓰고 싶으면 `"auth": "token"`을 명시한다.
대시보드는 `X-Perdix-Client` 헤더가 있고 Origin이 로컬일 때만 요청을 받아, 다른 웹사이트가
이 주소로 작업을 제출하지 못하게 한다. 같은 컴퓨터의 다른 프로그램은 이 헤더를 붙여 접속할
수 있으므로, 여러 사용자가 쓰는 컴퓨터나 원격 접속에는 쓰지 않는다.

브라우저에서 `http://127.0.0.1:18790`을 열면 바로 연결된다(`auth: "token"`이면 연결 토큰을
입력하며 토큰은 브라우저 탭의 sessionStorage에만 보관한다). 작업 제출, 최근 200개 작업, 실행 기록, 취소,
도구 권한 승인을 제공한다. 권한 요청은 10분 뒤 자동 거절된다. 기록은 원본 ACP
세션 DB와 별개인 `~/.perdix/gateway.db`에 append-only로 보관한다. 원격 사용은
인증한 SSH 터널/TLS 프록시를 사용하고 기본 loopback 바인딩을 유지한다.

## Telegram

1. [BotFather](https://core.telegram.org/bots/features#botfather)에서 봇을 생성한다.
2. 사용자 ID와 chat ID를 허용 목록에 등록한다. 개인 채팅에서는 둘이 보통 같지만
   그룹에서는 chat ID가 별도다. 허용한 사용자가 허용한 chat에서 보낸 요청만 받는다.
3. 봇과 대화를 시작한다. 기존 봇 Webhook은 제거해야 long polling이 가능하다.

```text
/projects
/run my-app 로그인 오류를 분석하고 수정해줘
/model
/model deepseek
/status 작업ID
/cancel 작업ID
```

접수 메시지는 생략하고 같은 chat에 답변 본문만 보낸다. 작업 ID와 완료 상태는
답변에 붙이지 않는다. 승인 요청은 웹 대시보드에서 처리한다.
일반 문장도 질문으로 받는다. 등록 프로젝트가 하나면 바로 전달하고, 여러 개면
본인이 같은 chat에서 마지막으로 요청한 프로젝트를 사용한다. 첫 요청에서 프로젝트를
결정할 수 없으면 `/run 프로젝트 질문`으로 선택하도록 안내한다.
같은 사용자·chat·프로젝트의 후속 질문은 이전 대화 세션을 이어서 사용한다.
워크플로 작업은 일반 대화 세션 재사용 대상에서 제외한다.
`/model`은 현재 모델과 모델 종류 버튼을 보여준다. 종류를 고른 뒤 원하는 모델
버튼을 누르면 된다. 목록은 10개씩 표시하며 이전·다음 버튼으로 이동한다.
`/model kimi`처럼 검색할 수도 있다. 모델 버튼을 누르면 다음 요청부터
적용된다. 선택 후에는 같은 메시지에 변경된 모델을 표시한다. 메뉴는 본인만
사용할 수 있으며 1시간 후 만료된다. 선택은 사용자·채팅·프로젝트별로 저장되어
재시작 후에도 유지되며,
진행 중인 작업에는 영향을 주지 않는다. 기존 대화는 유지하고 모델 인증은 기존
제공자별 인증 설정을 사용한다. 여러 프로젝트에서는 먼저 `/run 프로젝트 질문`으로
대상을 선택한다. 이 명령은 Perdix 프로젝트에서만 지원하며 외부 ACP 에이전트의
모델 설정은 변경하지 않는다.
상태·취소는 본인이 해당 chat에서 제출한 작업에만 허용한다. update ID를 영속 기록해
재전달·재시작으로 같은 요청이 중복 실행되지 않게 한다. Bot API 목적지는
`https://api.telegram.org`; 요청·결과 텍스트가 Telegram에 전달된다.
[공식 Bot API](https://core.telegram.org/bots/api#getupdates)

## Hermes · OpenClaw를 같은 봇에 연결

프로젝트별 `acp.command`에 ACP 서버 실행 명령을 등록하면 같은 Telegram 봇에서
여러 에이전트를 선택할 수 있다. `acp`를 생략한 프로젝트는 기존 Perdix를 사용한다.
아래는 `projects` 항목의 예시다. 작업 디렉터리는 자신의 절대 경로로 바꾼다.

```json
{
  "perdix": {
    "cwd": "/absolute/path/to/workspace"
  },
  "hermes": {
    "cwd": "/absolute/path/to/workspace",
    "acp": { "command": ["hermes", "acp"] }
  },
  "openclaw": {
    "cwd": "/absolute/path/to/workspace",
    "acp": { "command": ["openclaw", "acp"] }
  }
}
```

```text
/run hermes 이 코드의 개선점을 설명해줘
/run openclaw 현재 작업을 요약해줘
/run perdix 테스트를 작성해줘
```

이후 일반 메시지는 마지막으로 선택한 프로젝트로 전달한다. 사용자·채팅·프로젝트·
에이전트 실행 설정별로 대화를 분리하고, 해당 에이전트가 `session/load`를 지원하면
후속 요청에서 이전 세션을 복구한다. 지원하지 않으면 매번 새 세션을 만든다.
실행 명령이나 모드를 변경하면 새 대화가 시작된다.

Hermes는 [ACP 모드](https://hermes-agent.nousresearch.com/docs/user-guide/features/acp/)가
설치되고 해당 Hermes 프로필의 모델 인증이 유효해야 한다.
OpenClaw는 [ACP 브리지](https://docs.openclaw.ai/cli/acp)와 별도로 OpenClaw Gateway가
실행 중이어야 한다. 원격 연결은 `--url`과 `--token-file` 인수를 사용할 수 있다.
토큰 자체를 명령 배열에 넣지 않는다. 자동 실행 서비스에서는 실행 파일의 절대 경로를
사용하고 필요한 런타임이 `PATH`에 있는지 확인한다.

외부 에이전트의 모델·인증·메모리·도구·권한 정책은 해당 에이전트가 관리한다.
Perdix의 `server.provider`, `server.model`, `permission_mode`, `trust_project`를
외부 에이전트에 강제로 적용하지 않는다. 명시적인 초기 모드가 필요하면
`acp.mode`에 해당 에이전트가 지원하는 ACP 모드 ID를 지정한다.
외부 에이전트가 보내는 ACP 승인 요청은 기존 웹 대시보드에서 처리하며,
Perdix 전용 워크플로 제어는 외부 에이전트에 제공하지 않는다.

기존 Hermes/OpenClaw의 자체 Telegram 연동과 함께 사용할 때는 **서로 다른 봇 토큰**을
사용한다. 이 게이트웨이만 공용 봇을 수신하고 각 에이전트에는 ACP로 요청을 전달한다.
기존 봇·설정을 바꾸지 않고 별도 프로필을 연결할 수도 있다.

## GitHub

대시보드의 **GitHub 이슈 가져오기**에서 프로젝트와 이슈 번호를 선택할 수 있다.
저장소는 운영자가 프로젝트에 등록한 값만 사용한다. 공개 이슈는 토큰 없이 읽을 수
있고 비공개 이슈에는 해당 저장소의 Issues 읽기 권한이 필요하다.
[공식 이슈 API](https://docs.github.com/en/rest/issues/issues#get-an-issue)

자동 접수는 저장소 Settings → Webhooks에서:

- Payload URL: TLS 프록시로 노출한 `/webhooks/github`
- Content type: `application/json`
- Secret: `PERDIX_GITHUB_WEBHOOK_SECRET`와 같은 값
- Event: **Issue comments**

허용한 GitHub 계정이 등록 저장소의 이슈에 아래 댓글을 쓰면 접수한다.

```text
/perdix 이 이슈를 분석하고 수정한 뒤 검증해줘
```

원문 본문의 SHA-256 HMAC을 검증하며 댓글 ID로 중복을 막는다. PR 댓글·댓글 수정·
일반 댓글은 작업을 실행하지 않는다. 완료 댓글은 `post_results: true`인 경우에만
게시하며 Issues 쓰기 권한이 필요하다. 기본값에서는 외부 댓글을 게시하지 않는다.
GitHub 목적지는 `https://api.github.com`; 토큰은 Authorization 헤더로만 전달한다.
[공식 Webhook 서명 검증](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)

## 재시작과 현재 범위

화면 연결 해제는 작업을 취소하지 않는다. 게이트웨이를 종료하면 실행 중 작업도
취소한다. 비정상 종료 후 재시작에서는 미완료 작업을 `interrupted`로 기록하며
자동 재실행하지 않는다. 기록을 확인한 뒤 새 작업으로 제출한다.

1차 연동은 단일 운영자용이다. Telegram·GitHub 계정의 실제 연결에는 위 자격 증명과
설정이 필요하다. GitHub Webhook의 공개 TLS 주소, PR 생성 자동화, 여러 사용자별
권한 분리, 원격 실행 노드, 기존 CLI/VS Code 게이트웨이 접속 전환은 별도 범위다.
