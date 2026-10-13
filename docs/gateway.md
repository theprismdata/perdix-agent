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
| `PERDIX_GATEWAY_TOKEN` | 대시보드 Bearer 인증, 24자 이상 | 필수 |
| `PERDIX_TELEGRAM_TOKEN` | BotFather에서 만든 봇 토큰 | Telegram 활성화 |
| `PERDIX_GITHUB_TOKEN` | GitHub REST 접근 | 비공개 저장소 읽기·결과 댓글 |
| `PERDIX_GITHUB_WEBHOOK_SECRET` | GitHub Webhook HMAC 검증 | Webhook 접수 |

채널 비밀은 게이트웨이 메모리에서만 사용하며 ACP 자식 env에는 넘기지 않는다.
모델 키는 기존 `~/.perdix/credentials.json` 또는 `server.credentials_path`의 파일을
사용한다. 게이트웨이에서는 자식 env를 정리하므로 공급자 API 키 env 전달을 사용하지
않는다. 기존 OpenAI OAuth 자격 증명 파일도 서버에서 사용할 수 있다.

브라우저에서 `http://127.0.0.1:18790`을 열고 연결 토큰을 입력한다. 토큰은 브라우저
탭의 sessionStorage에만 보관한다. 작업 제출, 최근 200개 작업, 실행 기록, 취소,
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
/status 작업ID
/cancel 작업ID
```

접수와 완료를 같은 chat에 알린다. 승인 요청은 웹 대시보드에서 처리한다.
상태·취소는 본인이 해당 chat에서 제출한 작업에만 허용한다. update ID를 영속 기록해
재전달·재시작으로 같은 요청이 중복 실행되지 않게 한다. Bot API 목적지는
`https://api.telegram.org`; 요청·결과 텍스트가 Telegram에 전달된다.
[공식 Bot API](https://core.telegram.org/bots/api#getupdates)

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
