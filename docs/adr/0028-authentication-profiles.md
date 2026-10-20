# ADR 0028: 제공자별 인증 프로필과 대화 세션 분리

상태: 승인됨

## 배경

공급자마다 하나의 전역 인증만 사용하면 개인·업무 계정을 구분하기 어렵고,
기본 계정을 바꾼 뒤 과거 대화를 재개할 때 호출 계정도 달라진다.

## 결정

- Anthropic, OpenAI, Gemini, OpenRouter에 이름 있는 인증 프로필을 제공한다.
  API 키는 모두 지원하고 Anthropic은 ant 계정 로그인, OpenAI는 ChatGPT 로그인을 지원한다.
- 기존 credentials.json은 유지한다. 같은 디렉터리의 auth-profiles.json에는 프로필의
  제공자·인증 방식과 제공자별 기본 선택만 저장한다. 비밀은 auth-profiles/<이름>/에
  분리하고 0600 및 원자적 쓰기, 프로세스 간 잠금을 적용한다.
- 명시적으로 선택한 프로필에는 환경변수나 다른 계정으로의 fallback을 적용하지 않는다.
  legacy 선택만 기존 환경변수·credentials.json·기존 로그인 동작을 유지한다.
- 새 대화는 제공자별 기본 선택을 config_change 이벤트에 고정한다. 기존 대화는
  이벤트에서 복원하며, 기록이 없는 과거 대화는 legacy를 사용한다. 로그아웃은 대화를 지우지 않는다.
- agents.json의 역할 설정에 auth_profile을 선택적으로 지정한다. 역할 지정 > 대화 선택 순이다.
  지정한 프로필이 모델 제공자와 다르면 오류로 처리하고 다른 계정으로 대체하지 않는다.
- ACP 표준 session/set_config_option의 auth_profile_<provider>와 config_option_update를
  사용한다. /auth list|status|use 명령도 같은 경로를 사용한다. 비밀은 ACP로 보내지 않는다.
- CLI의 명명 프로필 인증은 Python 서버 명령에 위임한다. API 키는 stdin으로 전달한다.
  Anthropic 로그인은 ant의 perdix-<이름> 프로필을 참조하고 ExecBackend의 env 정리를 거친다.

## 영향

기존 설치와 설정을 자동 이동하거나 덮어쓰지 않는다. 프로필 단위 로그아웃 이후에도
이름과 선택은 남아 재로그인할 수 있다. 사용 가능 표시는 저장 상태이며 실제 호출 권한을 보장하지 않는다.
