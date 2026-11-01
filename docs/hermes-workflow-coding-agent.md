# 워크플로로 코딩 에이전트 만들기

Hermes Desktop의 Workflow 편집기로 **계획 → 구현 → 검토** 세 단계로 일하는 코딩 에이전트를 만든다.
완성하면 채팅에서 `/flow coder <요청>` 한 줄로 다른 저장소의 파일을 고치게 할 수 있다.

노드 추가, 연결, 저장 같은 편집기 기본 조작은 [Workflow 편집기 사용법](hermes-workflow.md)에 있다.
이 구조를 택한 이유는 [설계 결정 기록 0027](adr/0027-langflow-harness.md)에 있다.

## 구성 요소

이 문서에 나오는 것은 네 가지다.

| 이름 | 무엇인가 |
| --- | --- |
| **Langflow** | 플로우를 저장하고 실행하는 외부 서버. 노드 그래프를 받아 위에서 아래로 돌린다 |
| **perdix-relay** (중계) | Perdix가 띄우는 작은 HTTP 서버. Perdix의 역할 설정(프롬프트·모델·도구)과 모델 호출, 도구 실행을 Langflow에 제공한다. `127.0.0.1`에서만 받는다 |
| **Perdix Agent 노드** | 이 저장소의 Langflow 컴포넌트. 중계에 역할을 물어 **지시문, 모델, 도구** 세 가지를 출력한다. 스스로는 아무것도 실행하지 않는다 |
| **Agent 노드** | Langflow 기본 노드. 입력을 받아 모델 호출과 도구 사용을 반복해 답을 낸다. 실제 작업은 여기서 일어난다 |

Perdix Agent와 Agent는 항상 한 쌍으로 쓴다. Perdix Agent의 세 출력을 Agent의 세 입력에 이으면,
Agent가 Perdix의 해당 역할과 같은 설정으로 일한다.

```
            ┌─ 지시문 ─┐
Perdix Agent ├─ 모델 ───┤→ Agent → 응답
  (역할: build)└─ 도구 ───┘
```

### 실행 중에 일어나는 일

Perdix Agent가 건네는 셋 중 모델과 도구는 단순한 값이 아니라 중계로 이어지는 연결이다.
그래서 플로우가 도는 동안 중계가 계속 떠 있어야 한다.

```
                         perdix-relay (작업 폴더 = 고칠 저장소)
                         ┌──────────────────────────────────────────────┐
Perdix Agent ──────────▶ │ GET  /v1/agents/<역할>                        │  ① 설정 조회
   │ 설정 3개            │                                              │
   ▼                     │                                              │
Agent ─── 모델 호출 ───▶ │ POST /v1/chat/completions                     │  ② 모델 호출
   │  ─── 도구 호출 ───▶ │ POST /mcp/<역할>                              │  ③ 도구 실행
   ▼                     └──────────────────────────────────────────────┘
Response
```

| 단계 | 누가 | 하는 일 |
| --- | --- | --- |
| ① 설정 조회 | Perdix Agent → 중계 | 역할의 지시문, 모델, 도구 목록을 받는다 |
| ② 모델 호출 | Agent → 중계 | 중계가 Perdix에 설정된 공급자와 키로 역할에 할당된 모델을 부른다. 키는 중계 밖으로 나가지 않는다 |
| ③ 도구 실행 | Agent → 중계 | 중계가 권한 모드를 검사한 뒤 Perdix 내장 도구를 작업 폴더 안에서 실행한다 |

일을 반복하며 판단하는 쪽은 Agent이고, 모델 호출과 도구 실행은 중계를 거쳐 Perdix가 한다.

알아 둘 점이 셋 있다.

- **지시문에 고칠 저장소의 지침이 들어간다.** 중계는 고정된 글을 주지 않고, 자기 작업 폴더를 기준으로 Perdix 시스템 프롬프트를 만든다.
  그래서 고칠 저장소에 AGENTS.md 같은 프로젝트 지침이 있으면 그것도 지시문에 들어간다.
- **설정은 실행할 때마다 다시 받는다.** Perdix Agent는 플로우가 돌 때마다 중계에 역할을 묻는다.
  Perdix에서 역할의 모델 할당을 바꾸면 플로우를 고치지 않아도 다음 실행부터 반영된다.
  노드의 Model 칸에 모델을 고른 경우에는 그 노드만 할당 대신 고른 모델을 쓴다.
- **Agent는 자기 기본 도구를 덧붙인다.** Langflow Agent는 Perdix가 준 도구 외에 날짜 조회, 계산식 같은
  기본 도구를 스스로 붙인다. 파일을 건드리지 않는 도구들이다.

## 왜 코딩용 중계를 따로 띄우나

중계 하나는 시작할 때 정한 **작업 폴더**(`--workspace`)와 **권한 모드**(`--permission-mode`)로 동작한다.
도구는 그 작업 폴더 안에서만 실행된다.

| 권한 모드 | 통과하는 도구 |
| --- | --- |
| `deny` (기본) | read, glob, grep, ls |
| `accept-edits` | 위 도구와 write, edit |

코딩 에이전트는 파일을 써야 하지만, 읽기만 하는 다른 플로우까지 쓰기 권한을 가질 필요는 없다.
그래서 쓰기를 허용하는 중계를 따로 띄운다.

| 포트 | 권한 | 쓰는 곳 |
| --- | --- | --- |
| 18796 | `deny` | 읽기만 하는 다른 플로우. Perdix Agent 노드의 기본 주소다 |
| 18797 | `accept-edits` | 이 문서의 코딩 플로우. Perdix Agent 세 개 모두 |

이 문서의 플로우는 18797만 쓴다. 18796은 다른 플로우를 위해 이미 띄워 두었다면 그대로 두면 된다.
코딩 플로우의 계획·검토 단계도 18797에 붙지만, 그 역할들은 읽기 전용이라 쓰기 도구를 받지 않는다.

포트 번호는 예시다. 비어 있는 포트면 된다. 바꾸면 2절과 3-4의 번호를 같이 바꾼다.

## 제약

- **승인 창이 없다.** 플로우 안의 도구는 Langflow가 중계를 거쳐 실행한다. Perdix 채팅의 승인 카드,
  체크포인트, 검증 게이트는 적용되지 않는다. 되돌리기는 git으로 한다.
- **bash는 실행되지 않는다.** 중계는 "물어보기"로 정해진 도구를 승인 창 없이 처리할 수 없어서 거부한다.
  bash와 webfetch가 여기에 해당한다. 이 에이전트는 파일을 고치지만 테스트는 돌리지 못한다.
  테스트까지 맡기려면 플로우 대신 Perdix 채팅의 build 모드를 쓴다.

## 1. 준비

다음이 떠 있어야 한다. 설치와 실행은 각 문서를 따른다.

| 무엇 | 설명 |
| --- | --- |
| Langflow | UI 포함으로 실행하고, `LANGFLOW_COMPONENTS_PATH`가 이 저장소의 `integrations/langflow/components`를 가리켜야 팔레트에 Perdix Agent가 나온다. [langflow.md](langflow.md) |
| Perdix 호환 서버 | `--langflow-config`와 `--hermes-home`을 주고 실행해야 사이드바에 Workflow가 생긴다. [hermes-desktop.md](hermes-desktop.md) |
| Hermes Desktop | 위 서버에 원격 모드로 연결. [hermes-desktop.md](hermes-desktop.md) |

## 2. 코딩용 중계를 띄운다

**2-1. 고칠 저장소를 안전하게 둔다.** 에이전트의 변경은 바로 파일에 쓰인다.
시작 전에 그 저장소를 커밋하거나 작업 브랜치를 만들어 둔다.

**2-2. 중계를 띄운다.** 이 저장소의 `server` 폴더에서 실행한다. 고칠 저장소를 작업 폴더로 주고 쓰기를 허용한다.

```bash
PROJECT=/path/to/your-project     # 에이전트가 고칠 저장소
cd server
uv run python -m perdix.relay --port 18797 --workspace "$PROJECT" --permission-mode accept-edits
```

중계는 처음 실행할 때 `~/.perdix/relay-token`에 접속 토큰을 만든다. 이미 있으면 그 토큰을 쓴다.

**2-3. 제대로 떴는지 확인한다.** 다른 터미널에서 실행한다. 응답의 `tools`에 `write`, `edit`가 있으면 된다.

```bash
curl -s -H "Authorization: Bearer $(cat ~/.perdix/relay-token)" \
  http://127.0.0.1:18797/v1/agents/build
```

**2-4. 토큰을 Langflow에 등록한다.** Perdix Agent 노드는 Langflow 전역 변수 `PERDIX_RELAY_TOKEN`에서 토큰을 읽는다.
Langflow 화면 → Settings → Global Variables에서 이 이름으로 `~/.perdix/relay-token`의 값을 넣는다.
전에 다른 중계 때문에 등록해 두었다면 같은 토큰이므로 건너뛴다.

## 3. 플로우를 완성한다

아래 순서대로 하면 노드 여덟 개, 선 열세 줄짜리 플로우가 된다. 완성하면 이런 모양이다.

```
                 [Perdix Agent plan]   [Perdix Agent build]   [Perdix Agent reviewer]
                        │ 3줄                  │ 3줄                   │ 3줄
                        ▼                      ▼                       ▼
[Chat Input] ──▶ [Agent  plan 실행] ──▶ [Agent  build 실행] ──▶ [Agent  reviewer 실행] ──▶ [Chat Output]
```

편집기 기본 조작은 이것뿐이다.

- **노드 넣기:** 왼쪽 팔레트에서 이름을 클릭한다. 새 노드는 캔버스 왼쪽 위에 쌓인다.
- **옮기기:** 노드 제목줄을 끈다. 빈 캔버스를 끌어 여러 개를 고르면 함께 옮겨진다.
- **잇기:** 노드 오른쪽의 초록 점(출력)을 다른 노드 왼쪽의 파란 점(입력)으로 끈다.
- **값 바꾸기:** 노드를 클릭하면 오른쪽 패널에 값이 나온다. 자주 안 쓰는 값은 패널 아래 `고급`에 접혀 있다.
- **지우기:** 노드를 고르고 Delete 키.

### 3-1. 새 플로우를 만든다

Hermes Desktop 사이드바에서 **Workflow**를 누르고, 헤더의 `+ 새 플로우`를 눌러 이름 `coder`를 넣고 Enter.
빈 캔버스가 열린다.

### 3-2. 노드 여덟 개를 넣는다

팔레트 검색창에 이름을 쳐서 찾으면 빠르다.

| 노드 | 개수 | 하는 일 |
| --- | --- | --- |
| Chat Input | 1 | 플로우의 시작점. 사용자가 보낸 요청이 여기로 들어온다. 앞에 아무것도 잇지 않는다 |
| Perdix Agent | 3 | 역할 하나의 설정(지시문, 모델, 도구)을 내놓는다. 스스로 실행하지는 않는다 |
| Agent | 3 | 받은 설정으로 실제 일을 한다. 모델 호출과 도구 사용을 반복해 답을 낸다 |
| Chat Output | 1 | 플로우의 끝. 마지막 답을 돌려준다 |

### 3-3. 위 그림처럼 배치한다

Perdix Agent의 출력 점은 노드 오른쪽에, Agent의 입력 점은 노드 왼쪽에 있다.
그래서 **각 Perdix Agent를 짝이 될 Agent의 위, 조금 왼쪽**에 두면 선이 꼬이지 않는다.

- 아랫줄: 왼쪽부터 Chat Input, Agent, Agent, Agent, Chat Output.
- 윗줄: 각 Agent의 위에 Perdix Agent 하나씩. Agent보다 조금 왼쪽으로 민다.

### 3-4. Perdix Agent 세 개를 설정한다

Perdix Agent를 하나씩 클릭해 오른쪽 패널에서 두 값을 바꾼다.

| 위치 | Role (패널 위쪽) | Perdix Relay URL (`고급`을 펼치면 나옴) |
| --- | --- | --- |
| 왼쪽 | `plan` | `http://127.0.0.1:18797` |
| 가운데 | `build` | `http://127.0.0.1:18797` |
| 오른쪽 | `reviewer` | `http://127.0.0.1:18797` |

바꾸고 나면 제목줄이 `Perdix Agent  plan  :18797`처럼 바뀐다. 셋 다 `:18797`인지 확인한다.
`:18796`이 남아 있으면 그 노드는 아직 기본 주소를 가리킨다. 실행하면 연결 오류가 나거나, 18796이 떠 있으면 쓰기가 거부된다.

세 노드 모두 2절의 코딩용 중계를 쓰지만, 쓰기 도구는 `build` 역할만 받는다.
세 노드를 같은 중계에 두어야 모두 고칠 저장소를 읽고, 그 저장소의 프로젝트 지침을 지시문으로 받는다.

**모델은 비워 둔다(선택).** 오른쪽 패널의 **Model** 칸을 비워 두면 그 역할에 할당된 모델을 쓴다.
할당은 `~/.perdix/agents.json`에 있다. 특정 노드만 다른 모델로 돌리고 싶으면 Model 칸을 눌러 목록에서 검색해 고른다.
고르면 제목줄에 모델 이름이 붙고(예: `build :18797 gemini-3.8-flash`), `기본값` 버튼으로 다시 비울 수 있다.
어느 쪽이든 키는 Perdix에 등록된 것을 쓴다.

### 3-5. Perdix Agent를 짝 Agent에 잇는다

이 세 줄은 작업 결과를 넘기는 선이 아니다. Perdix Agent는 일을 하지 않고, 역할의 **설정**만 Agent에 건넨다.
Agent는 그 설정을 받아 3-6에서 이을 요청을 처리한다.

표의 한 줄이 선 하나다. 윗줄 Perdix Agent의 왼쪽 칸 이름 초록 점을 끌어, 아래 짝 Agent의 오른쪽 칸 이름 파란 점에 놓는다.

| Perdix Agent의 초록 점 | → | Agent의 파란 점 | 건네는 설정 |
| --- | --- | --- | --- |
| Instructions | → | Agent Instructions | 역할의 지시문(시스템 프롬프트) |
| Language Model | → | Language Model | 역할에 할당된 모델 |
| Tools | → | Tools | 역할이 쓸 수 있는 도구 목록 |

세 줄 중 하나라도 빠지면 그 자리는 Langflow 기본값으로 채워진다. 특히 Tools가 빠지면 파일을 읽거나 고치지 못한다.

세 쌍 모두 같은 방식으로 잇는다. 모두 아홉 줄이다.
다 이으면 Agent 제목줄에 `plan 실행`, `build 실행`, `reviewer 실행`이 붙는다.
표시가 없는 Agent가 있으면 아직 잇지 않은 것이다.

### 3-6. 단계를 차례로 잇는다

이번에는 실제 작업 내용이 흐르는 선이다. 요청이 왼쪽에서 오른쪽으로 흐르도록 아랫줄을 네 줄로 잇는다.
Agent 하나는 위에서 설정(3-5)을, 왼쪽에서 할 일(이 절)을 받는다.

| 출력 | → | 입력 | 뜻 |
| --- | --- | --- | --- |
| Chat Input · Chat Message | → | Agent(plan 실행) · Input | 요청을 계획 단계에 넘긴다 |
| Agent(plan 실행) · Response | → | Agent(build 실행) · Input | 계획을 구현 단계에 넘긴다 |
| Agent(build 실행) · Response | → | Agent(reviewer 실행) · Input | 구현 결과를 검토 단계에 넘긴다 |
| Agent(reviewer 실행) · Response | → | Chat Output · Inputs | 검토 결과를 최종 답으로 낸다 |

구현 단계는 계획만 받고 원래 요청은 받지 않는다. plan 역할은 보통 요청을 요약해 계획에 적는다.
원문이 꼭 필요하면 Prompt Template 노드로 요청과 계획을 합쳐 build 실행 Agent의 Input에 넣는다.

### 3-7. 쓰지 않는 점

편집기는 Langflow가 고급으로 분류한 입력 점(Context ID, Session ID, Output Schema 등)을 숨긴다.
그래서 이 플로우에서 보이는 점은 거의 다 잇게 된다. 남는 것은 Agent의 **Structured Response** 출력 하나다.
구조화된 답을 쓰지 않으므로 비워 둔다.

숨긴 입력도 다른 화면(Langflow 자체 화면)에서 연결하면 편집기에 다시 보인다.

### 3-8. 저장한다

헤더의 `저장`을 누른다. "저장했습니다."가 뜨면 Langflow에 반영된 것이다.
저장하기 전까지는 화면에만 있다.

**마지막 확인:**

- [ ] 노드가 여덟 개다.
- [ ] Perdix Agent 셋의 제목줄이 각각 `plan`, `build`, `reviewer`이고 모두 `:18797`이다.
- [ ] Agent 셋의 제목줄에 `plan 실행`, `build 실행`, `reviewer 실행`이 붙어 있다.
- [ ] Chat Input에서 Chat Output까지 아랫줄이 끊김 없이 이어져 있다.

## 4. 작은 요청으로 시험한다

결과를 눈으로 확인하기 쉬운 요청부터 보낸다. 플로우 id는 Langflow 화면의 주소창이나 API 목록에서 본다.
Langflow API 키는 Langflow 설정 파일의 `api_key_file`에 적은 파일에 있다([langflow.md](langflow.md)).

```bash
FLOW=<플로우 id>
LANGFLOW_API_KEY=<Langflow API 키>
curl -s -H "x-api-key: $LANGFLOW_API_KEY" -H "Content-Type: application/json" \
  -X POST "http://127.0.0.1:7861/api/v1/run/$FLOW?stream=false" \
  -d '{"input_value":"README.md 맨 끝에 \"## License\" 절과 MIT 한 줄을 추가해","output_type":"chat","input_type":"chat"}'
```

확인할 것은 셋이다.

- 고칠 저장소에서 `git diff`로 실제 변경.
- 18797 중계를 띄운 터미널의 로그에서 역할별 모델 호출과 도구 호출.
- 응답 끝에 붙은 reviewer 의견.

## 5. 채팅에서 부르게 등록한다

Perdix 호환 서버에 준 Langflow 설정 파일(`--langflow-config`)의 `flows`에 별칭을 추가한다.

```json
{
  "flows": { "coder": "<플로우 id>" },
  "flow_descriptions": { "coder": "plan → build → reviewer 코딩 에이전트 (write/edit 허용)" }
}
```

서버는 시작할 때 별칭을 읽으므로 Perdix 호환 서버를 다시 시작한다. 그다음 Hermes 채팅에 보낸다.

```
/flow coder src/config.py의 load 함수에 파일이 없을 때 빈 설정을 돌려주는 처리를 넣어
```

## 문제가 생기면

| 증상 | 원인 | 조치 |
| --- | --- | --- |
| `denied by Perdix permission policy (relay mode: deny)` | 구현 노드가 기본 중계(18796)를 쓴다 | 노드의 `고급`에서 Perdix Relay URL을 18797로 바꾼다(3-4) |
| `Connection refused` 또는 `All connection attempts failed` | 노드가 가리키는 중계가 떠 있지 않다 | 제목줄 포트를 확인한다. 18796이면 18797로 바꾸고, 18797이면 2절의 중계를 띄운다 |
| bash 호출이 거부된다 | 중계에는 승인 창이 없다 | 테스트는 직접 돌리거나 Perdix 채팅의 build 모드를 쓴다 |
| 다른 저장소를 읽는다 | 중계의 `--workspace`가 다르다 | 18797 중계를 고칠 저장소로 다시 띄운다 |
| `401` 또는 인증 오류 | Langflow 변수 `PERDIX_RELAY_TOKEN`이 토큰 파일과 다르다 | `~/.perdix/relay-token` 값을 다시 넣는다 |
| 팔레트에 Perdix Agent가 없다 | Langflow가 컴포넌트 폴더를 못 읽었다 | `LANGFLOW_COMPONENTS_PATH`를 확인하고 Langflow를 다시 시작한다 |
| 구현이 요청과 다르다 | 계획에 원래 요청이 빠졌다 | Prompt Template으로 요청과 계획을 함께 넘긴다 |
| 응답이 `<function=...>` 같은 텍스트다 | 모델이 도구 호출을 텍스트로 냈다 | 그 역할에 할당된 모델을 바꾸거나 `general` 역할로 시험한다 |

## 정리

코딩용 중계는 쓰기 권한이 있으므로 작업이 끝나면 끈다. 띄운 터미널에서 Ctrl+C를 누르거나 다음을 실행한다.

```bash
lsof -ti tcp:18797 | xargs kill
```
