# Langflow 연동 (선택)

Langflow는 별도 프로세스로 띄우고, Perdix는 공개 HTTP 계약으로만 호출한다. 기본은 꺼짐이다.
설계와 검증 결과: [ADR 0026](adr/0026-langflow-adapter.md).

## 켜기

`langflow.json`(경로는 자유)을 만들고 서버를 `--langflow-config`로 실행한다.

```json
{
  "enabled": true,
  "base_url": "http://127.0.0.1:7861",
  "api_key_file": "~/.perdix/langflow/api-key",
  "flows": { "echo": "28549500-69e9-465f-aa8a-4486102151e4" },
  "flow_descriptions": { "echo": "입력을 그대로 돌려주는 시험용 플로우" }
}
```

```bash
cd server && uv run perdix-server --langflow-config /절대/경로/langflow.json
```

- `flows`의 별칭만 `langflow_run` 도구로 호출할 수 있다(허용 목록). 켜려면 최소 하나가 필요하다.
- `api_key_file`은 한 줄짜리 키 파일이며 권한이 600이 아니면 시작을 거부한다.
- `base_url`은 기본적으로 loopback만 허용한다. 다른 주소는 `allow_remote: true`가 필요하다.
- Langflow 버전은 `min_version` 이상 `max_version` 미만(기본 1.13.0 이상 2.0.0 미만)만 호출한다.
  범위 밖이거나 Langflow가 아닌 서버면 `unsupported_version`/`wrong_instance`로 거절한다.
- 연속 연결 실패 3회면 30초간 호출을 멈춘다(`breaker_failures`, `breaker_seconds`).

## 도구

`langflow_run(flow, input, session_id?)` — 종류 `execute`라 기본 권한은 ask이고 plan 모드에서는
쓸 수 없다. 오류는 `Langflow <코드>: <메시지>`인 `is_error` 결과로 돌아간다. 코드는
`unknown_flow`, `invalid_input`, `unavailable`, `timeout`, `auth`, `flow_not_found`,
`execution`, `unsupported_version`, `wrong_instance`, `bad_response`이며 `unavailable`·
`timeout`만 재시도할 만하다.

## 사용자가 직접 실행

`/flows`는 허용 목록을, `/flow <별칭> <입력>`은 해당 플로우를 직접 실행한다. 모델과 승인 없이
서버가 실행하며 허용 목록·버전 확인·오류 정규화는 도구와 같다. 결과는 대화에 보이지만 모델의
맥락에는 들어가지 않는다. Hermes Desktop 연동은 [hermes-desktop.md](hermes-desktop.md).

## 편집 화면

Hermes Desktop 안에서 Langflow 편집 화면(캔버스)을 여는 방법은 [hermes-desktop.md](hermes-desktop.md)를 본다.

## Perdix 판 화면

기본 Langflow 화면에는 GitHub·Discord 표시와 `Get started` 안내가 있다. 이를 뺀 프런트엔드를
[packages/langflow-ui](../packages/langflow-ui/README.md)로 빌드해 `langflow run --frontend-path`로 쓸 수
있다(Langflow 소스는 수정하지 않고 공식 `customization` 확장점만 덮어쓴다). 결정: [ADR 0037](adr/0037-langflow-ui-overrides.md).

## 시험용 Langflow

```bash
~/.perdix/langflow/run.sh 7861   # 포트가 사용 중이면 시작하지 않는다
```

## Perdix 모델을 Langflow에서 쓰기

Perdix에 설정해 둔 모델(`agents.json`의 별칭·역할)을 Langflow의 **OpenAI Compatible** 공급자로 쓸 수 있다.
Perdix가 OpenAI 호환 API(`/v1/models`, `/v1/chat/completions`, 스트리밍·도구 호출 포함)를 로컬에 열고,
Langflow는 이 주소를 일반 OpenAI 호환 서버로 본다(Langflow 소스 수정 없음). 결정: [ADR 0038](adr/0038-openai-compatible-model-api.md).

```bash
export PERDIX_MODELS_TOKEN=$(openssl rand -hex 24)   # 24자 이상. 이 토큰이 곧 모델 사용 권한이다
perdix-modelapi --port 18793                         # 127.0.0.1 전용
```

Langflow 전역 변수(설정 → 변수)에 다음 두 개를 만든다.

| 변수 | 종류 | 값 |
| --- | --- | --- |
| `OPENAI_COMPATIBLE_BASE_URL` | Generic | `http://127.0.0.1:18793/v1` |
| `OPENAI_COMPATIBLE_API_KEY` | Credential | 위 토큰 |

그러면 Language Model 컴포넌트에서 `OpenAI Compatible` 공급자를 고르면 `economy`, `balanced`, `role:build` 등이
목록에 나타난다. Langflow는 기본으로 루프백 주소 접근을 막으므로 `LANGFLOW_SSRF_ALLOWED_HOSTS=127.0.0.1`을
Langflow 실행 환경에 지정해야 한다(`~/.perdix/langflow/run.sh`가 이미 지정한다).

주의: `temperature`, `top_p`, `tool_choice` 같은 필드는 무시한다. 구독/OAuth 인증 프로필을 쓰는 모델은
해당 약관의 허용 범위 안에서만 쓸 것.
