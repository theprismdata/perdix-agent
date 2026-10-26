# ADR 0026: Langflow 선택 연동 (어댑터)

상태: 초안 (어댑터 1단계 구현: 클라이언트·오류 정규화·`langflow_run`. 사용법은 [docs/langflow.md](../langflow.md))

## 배경

GUI에서 워크플로를 시각적으로 편집·실행하기 위해 Langflow를 선택 기능으로 붙인다.
Langflow는 업데이트가 잦고 내부 구조가 크므로, 소스를 수정하거나 내부를 import하면
업데이트마다 깨진다. 우선순위는 속도가 아니라 연결과 안정성이다.

## 결정

1. **Langflow는 무수정, 별도 프로세스.** 설치는 PyPI 릴리스(`pip install langflow==X`)를
   전용 venv에 한다. Perdix 서버·TUI·앱은 Langflow 없이 동작해야 하며 기본은 꺼짐이다.
2. **공개 계약으로만 통신한다.** HTTP(`/health`, `/api/v1/version`, `/api/v1/run/{flow}`,
   v2 workflow), MCP. `langflow` 내부 모듈을 import하지 않는다.
3. **어댑터는 `server/src/perdix/integrations/langflow/` 한 곳에 가둔다**(규칙 7과 같은 원칙).
   하네스·도구 코드에 Langflow 분기를 두지 않는다. 서버는 에디터를 알지 않는다(규칙 2).
4. **호출 경로.** 1단계는 Langflow가 노출하는 MCP를 기존 `mcp.json`으로 연결(코드 없음).
   2단계는 `langflow_run` 도구(`fetch` 종류, 권한 정책 적용). 입력은 실행 전 스키마 검증하고
   실패는 `is_error` 도구 결과로 돌려준다(규칙 5).
5. **Langflow → Perdix 방향**은 별도 패키지의 커스텀 컴포넌트가 Perdix 게이트웨이 HTTP를
   호출한다. Langflow의 컴포넌트 경로 설정으로 로드하므로 Langflow 소스를 고치지 않는다.
6. **신뢰 게이트.** 호출 가능한 플로우는 설정의 허용 목록에 있는 것만(규칙 9). API 키는
   env 정리 계약을 거치고 파일 권한 600으로 보관한다(규칙 12). 기본 바인딩은 loopback.
7. **SDK 의존 최소화.** 공식 `langflow-sdk` 대신 필요한 엔드포인트만 쓰는 얇은 httpx
   클라이언트를 둔다(아래 스파이크 결과).
8. **장애 격리.** 연결 실패, 타임아웃, 서킷 브레이커는 어댑터가 처리한다. Langflow가 죽어도
   TUI·앱은 영향이 없다.
9. **버전 정책.** 시작 시 `/api/v1/version`을 조회해 지원 범위 밖이면 기능을 끄고 경고한다.
   지원 범위는 문서에 고정한다. 계약 테스트(어댑터가 쓰는 API만)를 고정 버전과 최신 버전
   양쪽에 돌린다.
10. **용어.** 기존 "workflows"와 구분하려고 "Langflow 플로우"라 부른다.

## 스파이크 결과 (2026-10-26, macOS, Python 3.12.7, langflow 1.13.0)

- `pip install langflow==1.13.0`은 회사 TLS 검사 환경에서도 성공했다(`SSL_CERT_FILE` 사용, venv 1.9GB).
  단 venv의 기본 pip 24.2는 마커 평가 버그로 실패해서 `pip -U pip`가 먼저 필요하다.
- `LANGFLOW_API_KEY_SOURCE=env` + `LANGFLOW_API_KEY`는 슈퍼유저(`LANGFLOW_SUPERUSER`)가
  DB에 **이미 있어야** 통과한다. 처음 AUTO_LOGIN=true로 만든 DB를 재사용하면 403이 난다.
  키 없는 요청은 403, 키 있는 요청은 200.
- **포트가 사용 중이면 Langflow가 오류 없이 다음 포트로 옮긴다.** 같은 포트를 가정한
  헬스 체크가 다른 인스턴스에 붙을 수 있다. 어댑터는 설정한 URL이 기대한 인스턴스인지
  확인(버전·인증 응답)해야 하고, 기동 스크립트는 포트 점검을 먼저 한다.
- **`langflow-sdk` 0.4.0의 `list_flows()` 기본 호출이 서버 1.13.0과 맞지 않는다.**
  기본값 `get_all=False`는 페이지 응답(`{items,total,...}`)을 받는데 SDK는 리스트로 해석해
  `ValidationError`가 난다. `get_all=True`면 동작한다. 공개 SDK도 서버와 어긋날 수 있으므로
  결정 7과 계약 테스트가 필요하다.

## 플로우 실행 검증 (같은 환경, Chat Input → Chat Output 에코 플로우)

| 항목 | `POST /api/v1/run/{flow}` | `POST /api/v2/workflows` |
|---|---|---|
| 요청 | `input_value`, `input_type`, `output_type`, `session_id`, `tweaks` | `flow_id`, `input_value`, `tweaks`, `session_id`, `mode` (`sync`/`stream`/`background`) |
| 성공 응답 | `outputs[0].outputs[0].results.message.text`로 중첩, 메시지 객체가 큼 | `output.text`, `status`, `job_id`, `errors[]`, `has_errors`로 평평함 |
| 스트리밍 | `?stream=true`, 한 줄 JSON 이벤트 (`add_message`, `end`) | `mode: stream`, SSE (`vertices_sorted`, `build_start`, `end_vertex`, `output`, `end`) |
| 백그라운드 | 없음 | `mode: background` → `job_id` + `links`, `GET ?job_id=`로 상태, `/{job_id}/events`로 재생, `/stop` |
| 없는 플로우 | 404 `{"detail": "Flow identifier ... not found"}` | 404 `{"detail": {"error","code":"FLOW_NOT_FOUND","message","flow_id"}}` |
| 키 없음 / 틀림 | 403 두 메시지 (`An API key must be passed...`, `Invalid or missing API key`) | 403 `Invalid API key` |
| 컴포넌트 실행 오류 | **HTTP 500**, `detail`이 JSON 문자열 (`message`에 `Error building Component Boom`) | **HTTP 200**, `status: failed`, `has_errors: true`, `errors[0].code: EXECUTION_ERROR`, `details.error_type` |
| 스트림 중 오류 | `error` 이벤트 2개 (컴포넌트 메시지, 마지막에 `500: {...}` 문자열), HTTP는 200 | `error` 이벤트 후 `end_vertex(valid:false)`, `end` |
| 잘못된 `mode`/필수 필드 누락 | — | 422 (FastAPI 검증 형식) |

관찰과 시사점:

- 어댑터는 성공·실패를 HTTP 상태만으로 판단할 수 없다. v1은 실행 실패를 500, v2는 200 +
  `has_errors`로 돌려준다. 스트림은 둘 다 HTTP 200이므로 `error` 이벤트를 직접 처리해야 한다.
- 알 수 없는 `tweaks` 컴포넌트, 잘못된 `input_type`, 빈 본문은 **조용히 통과**한다(v1 200,
  v2도 입력이 비어 실행). 어댑터가 호출 전에 플로우 정의로 입력을 검증해야 한다.
- 오류 본문 형식이 엔드포인트마다 다르다(문자열 `detail`, 객체 `detail`, 422 배열). 어댑터는
  하나의 내부 오류 형식으로 정규화해 `is_error` 도구 결과(규칙 5)로 돌려준다.
- **v2 요청 스키마가 소스와 릴리스에서 다르다.** 로컬 소스 체크아웃(`v1.13.0` 이후 3커밋)은
  `inputs`·`background`·`stream`을 받지만, PyPI `1.13.0`은 이를 `422 extra_forbidden`으로
  거부하고 `input_value`·`mode`만 받는다. v2는 아직 변동이 크므로 기본 경로를 **v1 `/run`**으로
  하고, v2는 백그라운드·재개가 필요할 때 버전 확인 후에만 쓴다.
- 비동기 장기 실행은 v2 background(`job_id`, 이벤트 재생, 중지)가 유일하게 지원한다.

## 미결

- 데스크톱 앱에서 Langflow UI를 임베드하는 방식(웹뷰 vs 외부 브라우저), 프런트 서빙 여부
  (`--backend-only`로는 UI가 없다).
- 상주 실행(launchd) 구성과 업데이트 절차.
- LLM 컴포넌트가 들어간 실제 플로우의 지연·타임아웃 거동, Human-in-the-loop(`/resume`) 연동.
