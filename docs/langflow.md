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

## 시험용 Langflow

```bash
~/.perdix/langflow/run.sh 7861   # 포트가 사용 중이면 시작하지 않는다
```
