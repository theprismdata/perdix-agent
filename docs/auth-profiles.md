# 인증 프로필

개인·업무 계정에 이름을 붙여 저장하고 대화마다 사용할 인증을 선택할 수 있습니다.
프로필은 인증을, 대화 세션은 작업 기록을, 모델 설정은 역할별 모델을 관리합니다.

| 제공자 | 인증 방식 |
|---|---|
| Anthropic | API 키 또는 ant 계정 로그인 |
| OpenAI | API 키 또는 ChatGPT 로그인 |
| Gemini | API 키 |
| OpenRouter | API 키 |

## 등록과 기본 선택

```bash
perdix auth login --provider anthropic --profile work-claude
perdix auth login --provider openai --profile personal-gpt
perdix auth login --provider openai --profile work-gpt --api-key YOUR_KEY
perdix auth login --provider gemini --profile personal-gemini --api-key YOUR_KEY
perdix auth login --provider openrouter --profile work-router --api-key YOUR_KEY

perdix auth list
perdix auth status --provider openai --profile personal-gpt
perdix auth use --provider openai --profile personal-gpt
```

Anthropic도 `--api-key`를 주면 API 키 프로필을 만듭니다. 키를 생략한 Anthropic은
공식 ant CLI가 필요하고, OpenAI는 브라우저에서 ChatGPT로 로그인합니다.
등록한 프로필의 제공자와 인증 방식은 고정되며, 다른 방식을 쓰려면 새 이름으로 등록합니다.

기본 선택은 **새 대화**에 적용됩니다. 기존 대화를 재개하면 그 대화의 선택을 복원합니다.
한 대화에서 다른 제공자의 모델로 바꾸면 해당 제공자에 선택한 인증을 사용합니다.

## 대화에서 확인·변경

CLI와 VS Code 채팅창에서 다음 명령을 사용합니다.

```text
/auth list
/auth status
/auth use openai work-gpt
/auth use gemini personal-gemini
```

변경은 현재 대화에만 저장됩니다. 작업이 실행 중이면 인증 변경을 거절합니다.
모델은 `/model`로 별도 선택합니다. 프로필을 바꿔도 모델은 바뀌지 않습니다.
상태의 `저장됨`은 자격 증명의 저장 상태이며 실제 호출 권한·사용량 확인 결과는 아닙니다.

## 역할별 지정

`~/.perdix/agents.json`의 역할에 `auth_profile`을 지정할 수 있습니다.

```json
{
  "agents": {
    "general": {"model": "claude-sonnet-5-5", "auth_profile": "work-claude"},
    "explore": {"model": "gemini-3.8-flash", "auth_profile": "personal-gemini"},
    "reviewer": {"model": "gpt-6.1-sol", "auth_profile": "work-gpt"}
  }
}
```

역할별 지정이 대화의 제공자별 선택보다 우선합니다. 모델 제공자와 프로필 제공자가
다르면 오류를 반환합니다. `/auth status`는 현재 메인 역할의 우선 지정도 표시합니다.
설정 파일 변경 후 서버를 다시 시작하세요.

## 로그아웃과 기존 설정

```bash
perdix auth logout --provider openai --profile personal-gpt
perdix auth use --provider openai --profile legacy
```

로그아웃해도 프로필 이름·선택과 대화 기록은 남습니다. 같은 이름으로 다시 로그인할 수
있으며 인증 실패·로그아웃 시 다른 계정으로 자동 전환하지 않습니다.

`legacy`는 기존 인증입니다. 기존 환경변수·`credentials.json`·기존 로그인 기록은 그대로
유지합니다. 이름 있는 프로필에는 환경변수 API 키가 덮어쓰지 않습니다. 프로필 기록이 없는
과거 대화는 `legacy`로 복원합니다. `--profile` 없는 기존 login/logout 명령도 기존 인증을 다룹니다.

메타데이터는 `~/.perdix/auth-profiles.json`, 비밀은 `~/.perdix/auth-profiles/<이름>/`에
저장됩니다. Anthropic 계정 로그인은 ant의 `perdix-<이름>` 프로필을 참조합니다.
명명 프로필 명령의 `--credentials <경로>`로 저장 기준 디렉터리를 바꿀 수 있습니다.
토큰·키는 대화나 프로토콜 응답에 포함하지 않습니다.
