# ADR 0024: OpenRouter 모델 조회와 역할 할당

상태: 채택

공식 모델 목록 조회와 agents.json 저장은 Python 서버가 담당한다.
ACP session/set_config_option으로 openrouter_catalog=refresh와
agent_model_<role>=<모델 ID>를 처리한다. 새 확장 메서드는 만들지 않는다.
공급자별 응답은 llm/openrouter.py에서 검증하고 text 출력·tools 지원 모델을
선택 가능하게 한다. 등록된 컨텍스트·출력 한도는 하네스에서도 사용한다.
사용자가 메뉴를 열 때만 조회하고 오류는 표시하며 기존 설정을 보존한다.
역할별 전용 별칭으로 저장해 다른 역할의 공유 별칭을 바꾸지 않는다.
설정은 같은 디렉터리의 임시 파일을 원자적으로 교체한다. 현재 역할이면
대기 중 세션에도 적용하고 실행 중 변경은 거절한다. 복원 세션 모델은 유지한다.
모델 한도·가격·effort 지원 정보는 별칭의 capabilities에 저장해 재시작 후에도 사용한다.
UI는 기존 Hermes ModelPicker에 역할 선택 단계만 추가한다.
