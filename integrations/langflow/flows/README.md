# Perdix 샘플 플로우

Langflow `POST /api/v1/flows/` 본문 그대로의 JSON이다. 노드의 `code` 필드는 Langflow가 컴포넌트를
다시 빌드하는 데 쓰므로 지우지 않는다.

## perdix-sample.json
Chat Input → **Perdix Agent**(role=`general`) → Agent → Chat Output.
Perdix Agent 노드가 `perdix-relay`(127.0.0.1:18796)에서 역할의 시스템 프롬프트·모델·도구를 받아 표준
Agent 노드의 Agent Instructions / Language Model / Tools에 꽂는다. 중계를 `--permission-mode deny`로
띄우면 읽기 전용 도구(`read`, `glob`, `grep`, `ls`, `websearch`, `webfetch`)만 통과한다.

역할을 `general`로 둔 이유: `explore`에 할당된 qwen3-coder-flash는 OpenRouter 경유 시 도구 호출을
함수 호출이 아니라 `<function=read>` 텍스트로 내놓는 일이 잦아(3회 중 2회) Langflow Agent가 도구를
실행하지 못한다. general(gemini-3.8-flash)은 3회 모두 정상 호출했다. 역할은 노드 속성에서 바꾸면 된다.

Hermes Desktop의 Workflow 페이지에서 같은 플로우를 손으로 만들 수 있다: "+ 새 플로우" → 팔레트에서
Chat Input, Perdix Agent, Agent, Chat Output 추가 → 출력 점(초록)을 입력 점(파랑)으로 끌어 5개 연결 →
Perdix Agent 선택 후 Role 지정 → 저장.

가져오기:
```bash
curl -H "x-api-key: $(cat ~/.perdix/langflow/api-key)" -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:7861/api/v1/flows/ -d @integrations/langflow/flows/perdix-sample.json
```
응답의 `id`를 `langflow.json`의 `flows`에 별칭으로 등록하면 Hermes 채팅에서 `/flow <별칭> <입력>`으로,
에이전트는 `langflow_run` 도구로 실행한다. 실행 전에 `perdix-relay`가 떠 있어야 한다
(`PYTHONPATH=server/src server/.venv/bin/python -m perdix.relay --workspace <저장소> --permission-mode deny`).
Langflow는 `LANGFLOW_COMPONENTS_PATH`가 이 저장소의 `integrations/langflow/components`를 가리켜야
Perdix Agent 컴포넌트를 로드한다.
