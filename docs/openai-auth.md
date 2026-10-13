# OpenAI 인증

OpenAI 모델 호출은 `OPENAI_API_KEY` → `~/.perdix/credentials.json`의
`openai.api_key` → ChatGPT OAuth 순서로 인증한다. 빈 키는 없는 것으로 처리한다.
API 키 오류는 OAuth로 자동 전환하지 않는다.

Windows 설치에서도 OpenAI는 API 키를 비워 둘 수 있다. 설치 후 OpenAI 모델의
첫 요청이나 위 로그인 명령에서 ChatGPT 인증을 진행한다.

## API 키가 없을 때

CLI와 VS Code에서 OpenAI 모델의 첫 요청을 보내면 브라우저에서 ChatGPT 로그인이
시작된다. 계정 로그인과 Perdix의 플랜 사용 권한 승인을 완료하면 요청이 계속된다.
이미 로그인했으면 저장된 토큰을 사용하고 만료에 가까워지면 자동 갱신한다.
요청 취소는 대기 중인 로그인도 중단한다. 로그인 제한 시간은 5분이다.

명시적으로 로그인하거나 갱신 실패 후 다시 인증할 수 있다:

```bash
perdix auth login --provider openai
perdix auth status --provider openai
perdix auth logout --provider openai
```

로그아웃은 저장된 API 키도 제거한다. 환경변수는 현재 쉘에서 직접 해제해야 한다.
OAuth 원격 해제를 확인하지 못하면 로컬 토큰을 지우고 별도로 안내한다.
Codex 확장의 기존 로그인 기록은 읽거나 변경하지 않는다.

## API 키가 있을 때

```bash
perdix auth login --provider openai --api-key YOUR_API_KEY
```

저장된 키나 환경변수 키가 있으면 브라우저를 열지 않는다. 키를 제거하면 저장된
ChatGPT 로그인으로 돌아간다. 유효하지 않은 키에 대한 오류도 그대로 보고한다.

## 저장과 전송

Python 서버가 인증을 처리한다. OAuth 토큰은 API 키 파일과 같은 디렉터리의
`openai-oauth.json`에 원자적으로 저장하고 Unix 파일 권한은 0600이다.
계정과 등록 client ID는 로그아웃 후에도 재로그인을 위해 남긴다. `openai-host-id`는
동일 호스트의 등록 식별자다. `--credentials PATH`를 쓰면 해당 디렉터리에 저장한다.
토큰 갱신은 프로세스 간 잠금으로 직렬화하며 도구 프로세스에 토큰을 전달하지 않는다.

인증과 토큰 갱신은 `auth.openai.com`, 추론은 `https://api.openai.com/v1/responses`로
보낸다. 모델 호출에는 시스템 지침, 모델에 필요한 대화·코드 맥락, 도구 정의와 결과가
포함된다. OAuth 요청은 `store:false`, `stream:true`를 사용한다.
ChatGPT 플랜·계정이 허용하는 모델과 사용량 한도가 적용된다. 이 경로는 preview이며
실제 계정 접근 가능 여부는 완료된 추론 요청으로 확인해야 한다.

브라우저와 콜백은 서버가 실행되는 컴퓨터에 있어야 한다. 원격 서버에서는 해당
컴퓨터에서 먼저 로그인해야 한다. 로그인 URL이나 토큰을 대화에 붙여 넣지 않는다.

공식 문서: [로그인](https://developers.openai.com/siwc/token-sharing-open-source/sign-in),
[모델 호출](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference),
[preview 제한](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
