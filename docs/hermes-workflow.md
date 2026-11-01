# Hermes Desktop Workflow 사용법

Hermes Desktop의 Workflow 페이지에서 Langflow 플로우를 만들고, Perdix 역할을 노드로 꽂고, 채팅에서 실행하는 순서를 정리한다.
설계 배경은 [ADR 0027](adr/0027-langflow-harness.md), 샘플 플로우는 [integrations/langflow/flows/](../integrations/langflow/flows/)에 있다.

## 1. 준비 (한 번)

```bash
~/.perdix/hermes-desktop/start-server.sh
```

이 스크립트가 다음 셋을 함께 띄운다. 이미 떠 있으면 건너뛴다.

| 프로세스 | 포트 | 역할 |
| --- | --- | --- |
| Langflow | 7861 | 플로우 저장·실행 |
| perdix-relay | 18796 | Perdix 역할의 프롬프트·모델·도구를 Langflow에 제공 |
| Hermes 호환 서버 | 18795 | Hermes Desktop 앱이 붙는 서버. Workflow 플러그인을 설치한다 |

그 다음 Hermes Desktop 앱을 열고 왼쪽 사이드바에서 **Workflow**를 누른다.

## 2. 화면 구성

- **헤더**: 플로우 선택, `+ 새 플로우`, `플로우 삭제`, 상태 문구, `저장`.
- **왼쪽 열(컴포넌트)**: Perdix, 입출력, 모델·에이전트, LLM 작업, 도구, 흐름 제어, 처리, 데이터 소스가 펼쳐져 있고 나머지는 `기타`로 접혀 있다. 검색창으로 전체를 찾는다.
- **가운데(캔버스)**: 노드와 연결선. 노드 제목을 끌어 옮기고, 빈 곳을 끌면 여러 노드를 고른다. 고급 입력 점은 연결돼 있을 때만 보인다. Perdix Agent 제목줄에는 고른 역할과 중계 포트가 붙고(예: `build :18797`), 그 Perdix Agent에 이어진 Agent에는 `build 실행`처럼 맡은 역할이 붙는다.
- **오른쪽 열**: 노드를 고르기 전엔 Perdix 역할 목록(읽기 전용), 하나를 고르면 속성 편집(드물게 쓰는 값은 아래 `고급`에 접혀 있다), 여럿을 고르면 개수와 일괄 삭제.

## 3. 플로우 만들기

1. `+ 새 플로우`를 누르고 이름을 넣은 뒤 Enter. 빈 캔버스가 열린다.
2. 왼쪽 팔레트에서 **Chat Input**, **Perdix Agent**, **Agent**, **Chat Output**을 차례로 클릭한다. 캔버스 왼쪽 위에 쌓이므로 제목을 잡고 끌어서 펼친다.
3. 초록 점(출력)을 파란 점(입력)으로 끌어 연결한다. 기본 구성은 다섯 개다.

   | 출력 | → | 입력 |
   | --- | --- | --- |
   | Chat Input · Chat Message | → | Agent · Input |
   | Perdix Agent · Instructions | → | Agent · Agent Instructions |
   | Perdix Agent · Language Model | → | Agent · Language Model |
   | Perdix Agent · Tools | → | Agent · Tools |
   | Agent · Response | → | Chat Output · Inputs |

4. **Perdix Agent** 노드를 클릭하고 오른쪽에서 **Role**을 고른다. 역할에 따라 시스템 프롬프트, 모델, 도구가 바뀐다.
5. 헤더의 `저장`을 누른다. "저장했습니다."가 뜨면 Langflow에 반영된 것이다. 저장 전까지는 화면에만 있다.

### 선택·이동·삭제

| 하려는 것 | 방법 |
| --- | --- |
| 하나 선택 | 노드 클릭 |
| 여러 개 선택 | Shift(또는 Cmd) 클릭으로 더하거나 빼기. 빈 캔버스를 끌면 사각형 안의 노드가 선택된다 |
| 전체 선택 / 선택 해제 | Cmd+A / Esc 또는 빈 곳 클릭 |
| 함께 옮기기 | 선택된 노드 중 하나의 제목을 끈다 |
| 지우기 | Delete 또는 Backspace. 오른쪽 패널의 빨간 `삭제` 버튼도 된다. 붙어 있던 연결선도 함께 지워진다 |

지운 것도 `저장`을 눌러야 Langflow에 반영된다. 저장 전이면 플로우를 다시 골라 되돌릴 수 있다.

### 플로우 지우기

플로우를 고른 뒤 헤더의 `플로우 삭제`를 누르면 같은 자리에 "지울까요?"와 빨간 `삭제` 버튼이 나온다.
`삭제`를 누르면 Langflow에서 바로 지워지고 되돌릴 수 없다.

채팅 별칭(`langflow.json`의 `flows`)이 그 플로우를 가리키고 있었다면 상태 문구에 끊긴 별칭이 나온다.
설정 파일은 편집기가 고치지 않으므로 그 별칭을 직접 지운다.

플로우를 고르지 않은 채 팔레트를 누르면 "먼저 플로우를 선택하세요."가 뜬다.

### 역할 고르기

| Role | 도구 | 비고 |
| --- | --- | --- |
| general | 읽기 전용 + 웹 | 샘플 기본값. 도구 호출이 안정적이다 |
| explore | 읽기 전용 | 할당 모델(qwen3-coder-flash)이 OpenRouter 경유 시 도구 호출을 텍스트로 내놓는 일이 잦다 |
| plan, reviewer, security-reviewer | 읽기 전용 | 비용이 높은 모델이 할당돼 있다 |
| build, executor, debugger | 쓰기 포함 | 중계가 `--permission-mode deny`면 쓰기 도구는 거부된다 |

### 모델 고르기

Perdix Agent의 **Model** 칸을 비워 두면 역할에 할당된 모델(`~/.perdix/agents.json`)을 쓴다.
칸을 누르면 중계가 아는 모델 목록이 뜨고, 검색해 고르면 그 노드만 그 모델로 호출한다. `기본값` 버튼으로 다시 비운다.
키는 어느 쪽이든 Perdix에 등록된 것을 쓴다.

예전에 만든 Perdix Agent 노드에는 Model 칸이 없다. 노드를 지우고 팔레트에서 다시 넣으면 생긴다.

중계가 어떤 모델과 도구를 주는지는 다음으로 확인한다.

```bash
curl -H "Authorization: Bearer $(cat ~/.perdix/relay-token)" http://127.0.0.1:18796/v1/agents/general
```

## 4. 실행하기

### 채팅에서 (`/flow`)

채팅에서 쓰려면 플로우에 **별칭**이 있어야 한다. `~/.perdix/hermes-desktop/langflow.json`의 `flows`에 적는다.

```json
{
  "flows": { "sample": "<플로우 id>" },
  "flow_descriptions": { "sample": "설명" }
}
```

플로우 id는 Langflow API 목록에서 본다.

```bash
curl -s -H "x-api-key: $(cat ~/.perdix/langflow/api-key)" \
  "http://127.0.0.1:7861/api/v1/flows/?remove_example_flows=true" | python3 -c "import json,sys;[print(f['id'],f['name']) for f in json.load(sys.stdin)]"
```

적은 뒤 `start-server.sh`를 다시 실행한다(Perdix 서버가 시작할 때 별칭을 읽는다). 그 다음 Hermes 채팅에 보낸다.

```
/flow sample 이 저장소 AGENTS.md의 경계 규칙 6번 제목을 한 줄로 답해
```

`/flows`는 등록된 별칭 목록이다. 에이전트도 같은 별칭을 `langflow_run` 도구로 부를 수 있다.

### 바로 시험 (API)

별칭 없이 돌려 볼 때는 Langflow API를 직접 부른다.

```bash
curl -H "x-api-key: $(cat ~/.perdix/langflow/api-key)" -H "Content-Type: application/json" \
  -X POST "http://127.0.0.1:7861/api/v1/run/<플로우 id>?stream=false" \
  -d '{"input_value":"질문","output_type":"chat","input_type":"chat"}'
```

## 5. 샘플 가져오기

저장소의 샘플(Chat Input → Perdix Agent(general) → Agent → Chat Output)을 Langflow에 넣는다.

```bash
curl -H "x-api-key: $(cat ~/.perdix/langflow/api-key)" -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:7861/api/v1/flows/ -d @integrations/langflow/flows/perdix-sample.json
```

응답의 `id`를 4절대로 별칭에 등록한다.

## 6. 문제가 생기면

| 증상 | 원인 | 조치 |
| --- | --- | --- |
| "Langflow 연동이 꺼져 있습니다" | 서버가 `--langflow-config` 없이 떴다 | `start-server.sh`로 다시 띄운다 |
| "Langflow가 꺼져 있거나 응답하지 않습니다" | 7861이 안 떠 있다 | `~/.perdix/langflow/server.log` 확인 |
| 실행 시 `Connection refused` | 중계(18796)가 없다 | `start-server.sh`가 띄운다. 로그는 `~/.perdix/logs/relay.log` |
| `unknown role: ...` | 컴포넌트와 중계의 역할 이름이 어긋났다 | 노드를 지우고 팔레트에서 다시 넣는다(새 역할 목록을 받는다) |
| 응답이 `<function=read>...` 텍스트 | 모델이 도구 호출을 텍스트로 냈다 | Role을 general로 바꾼다 |
| 팔레트에 Perdix 항목이 없다 | Langflow가 컴포넌트 경로를 못 읽었다 | `~/.perdix/langflow/run.sh`의 `LANGFLOW_COMPONENTS_PATH`가 `integrations/langflow/components`를 가리키는지 확인 |

코딩(파일 수정) 에이전트를 만드는 순서는 [hermes-workflow-coding-agent.md](hermes-workflow-coding-agent.md)에 있다.

## 7. 한계

- 플로우 안의 도구는 Langflow가 실행한다. Perdix의 권한 승인, 체크포인트, 검증 게이트는 적용되지 않는다. 쓰기 도구는 중계의 `--permission-mode`로만 막는다.
- 이 편집기는 노드 추가·이동·삭제, 연결, 값 편집, 저장만 지원한다. 파일 업로드 필드와 노드 안에서 실행 결과 보기는 Langflow 자체 화면을 쓴다.
- 별칭 등록은 아직 설정 파일을 손으로 고쳐야 한다.
