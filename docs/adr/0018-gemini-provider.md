# ADR 0018: Gemini 공급자 연결

상태: 채택

## 결정

기존 OpenAI·Anthropic 어댑터에 Gemini 네이티브 REST 어댑터를 추가한다.
`streamGenerateContent` SSE를 공통 ModelEvent로 변환하며 기존 httpx2를 사용한다.
도구 실행·권한·재시도는 기존 Python 하네스가 담당한다.

Gemini 원본 Part와 thoughtSignature는 OpaqueItem에 보존하고 같은 공급자·모델에만
재전송한다. 다른 공급자·모델의 도구 이력은 텍스트로 변환하여 서명 없는 Gemini
functionCall을 만들지 않는다. 도구 결과는 원래 호출 순서를 유지한다.

인증은 GEMINI_API_KEY 또는 ~/.perdix/credentials.json의 gemini.api_key를 사용한다.
키가 없거나 호출이 실패하면 오류를 반환하며 다른 공급자로 자동 우회하지 않는다.
역할 할당은 기존 ~/.perdix/agents.json과 ACP 모델 선택을 그대로 사용한다.

## 근거

- https://ai.google.dev/api/generate-content
- https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash

저장 포맷·ACP 메서드를 추가하지 않는다. 기존 OpaqueItem 확장 필드를 사용한다.
