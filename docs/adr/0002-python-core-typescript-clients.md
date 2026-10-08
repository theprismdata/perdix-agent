# ADR 0002: 에이전트 코어는 Python, 클라이언트는 TypeScript

- 상태: 채택 (2026-10-07)

## 결정
하네스 루프, LLM 호출, 도구 실행, 권한, 세션 저장은 전부 Python(`server/`)에 둔다. TypeScript(`packages/`)는 UI와 에디터 연동만 담당한다.

## 근거
- 코어가 한 곳에 있어야 클라이언트가 늘어도(TUI, VS Code, 향후 Webview/Zed) 동작이 갈라지지 않는다.
- Hermes Agent의 툴셋/메모리/압축 설계를 Python 생태계에서 그대로 참고할 수 있다.

## 결과
- Python 런타임 배포 문제가 생긴다(uv tool vs 단일 바이너리). 별도 ADR로 결정한다.
- VS Code의 저장 안 된 버퍼는 ACP `fs/*` capability로 클라이언트가 서빙한다. 서버는 에디터를 직접 알지 않는다.
