# ADR 0033: Ollama 로컬 공급자

상태: 채택

## 결정

Ollama를 별도 공급자로 추가하고 로컬 OpenAI 호환 Responses API를 사용한다.
기본 엔드포인트는 `http://127.0.0.1:11434/v1`이며 CLI의
`--ollama-base-url`로 다른 Ollama 서버를 지정할 수 있다.

Ollama는 인증 프로필과 API 키를 사용하지 않는다. 기존 OpenAI 어댑터의
ModelView, 도구 스키마, Responses 스트림 이벤트 변환은 재사용하되 공급자 생성과
엔드포인트 선택은 `llm/ollama.py` 안에 둔다. 모델 ID는 Ollama 태그를 그대로 쓰며
카탈로그에 없는 모델도 `--model`로 선택할 수 있다.

ACP 메서드와 저장 스키마는 변경하지 않는다. 로컬 모델의 가격은 0으로 기록하고,
Ollama Responses API가 지원하지 않는 OpenAI 전용 encrypted reasoning include는 보내지
않는다.

## 근거

- https://docs.ollama.com/api/openai-compatibility
- https://docs.ollama.com/capabilities/tool-calling
- https://docs.ollama.com/context-length
