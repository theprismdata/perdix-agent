# ADR 0011: OpenAI API 키 우선, ChatGPT OAuth 대체 인증

- 상태: 승인됨
- 날짜: 2026-10-12

## 결정

OpenAI 요청은 `OPENAI_API_KEY`, Perdix credentials의 API 키 순서로 선택한다.
둘 다 없을 때만 Perdix 자체 ChatGPT OAuth 등록을 사용한다. API 키 인증 실패를
OAuth로 우회하지 않는다. Codex 확장의 토큰이나 등록 client ID를 복사하지 않는다.

Python 서버가 PKCE, loopback 콜백, ID 토큰 서명/issuer/audience/nonce 검증,
토큰 저장과 갱신을 소유한다. CLI는 서버 인증 명령에 위임하고 ACP는 표준
`authenticate`를 사용한다. 첫 OpenAI 요청에 로그인이 필요하면 브라우저를 열고
채팅 스트림으로 안내한다. 취소와 제한 시간 종료는 로그인도 중단한다.

OAuth 기록은 API 키 파일과 별도로 동일 디렉터리의 `openai-oauth.json`에
원자적으로 기록하고 Unix에서 0600을 적용한다. 프로세스 간 잠금으로 회전하는
refresh token의 중복 사용을 막는다. 토큰을 도구 env, 대화 이력, 로그에 넣지 않는다.

OAuth 추론은 공개 Responses API를 사용한다. `store:false`, `stream:true`,
전체 ModelView input, 지원하는 도구 형식을 적용하고 지원하지 않는 필드를 뺀다.
키 인증의 요청 형식은 유지한다. 플랜 제한이나 갱신 실패는 오류로 보고한다.

## 근거

- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
- https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions

## 검증 경계

로컬 콜백과 HTTP 경계 테스트로 우선순위, PKCE, 서명 검증, 갱신,
취소와 요청 형식을 검증한다. 실제 OpenAI 계정 승인은 사용자가 브라우저에서
완료해야 하며 실제 플랜 접근 가능 여부는 완료된 추론 요청으로만 확인된다.
