# ADR 0019: OpenRouter 연결

상태: 채택

## 결정

OpenRouter를 별도 공급자로 추가하고 네이티브 Chat Completions SSE를 사용한다.
기존 OpenAI Responses·ChatGPT OAuth 경로와 인증을 분리한다.
OPENROUTER_API_KEY 또는 credentials.json의 openrouter.api_key로 인증하며
실패 시 직접 공급자나 OAuth로 우회하지 않는다.

기존 ModelView와 도구·권한·역할 할당을 유지한다. 같은 OpenRouter 모델의
reasoning_details만 OpaqueItem으로 보존·재생한다. 스트림이 끊기거나 도구 JSON이
불완전하면 호출을 실행하지 않는다. 스트림의 마지막 usage는 한 번만 집계한다.

공급자별 모델 ID(openai/…, anthropic/…, google/…, qwen/…)는 직접 연결 모델과
구별된다. 카탈로그와 agents.json의 별칭을 통해 공급자를 선택한다.
OpenRouter 내부의 엔드포인트 라우팅은 OpenRouter가 담당한다.

ACP 메서드와 저장 스키마는 변경하지 않는다. API 키는 기존 비밀 env 정리 계약을 따른다.

## 근거

- https://openrouter.ai/docs/api_reference/overview
- https://openrouter.ai/docs/api_reference/streaming
- https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
- https://openrouter.ai/api/v1/models (2026-10-13 카탈로그 확인)
