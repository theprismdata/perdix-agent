# Hermes 원본

- 저장소: https://github.com/NousResearch/hermes-agent
- 로컬 원본: `/Users/prismdata/Documents/oss/agent/hermes-agent`
- 고정 커밋: `27c02f6326e82b2ba184f5a849f0c5b3a38efb9c`
- 라이선스: `vendor/LICENSE` (MIT)
- 가져온 경로: `ui-tui/src`, `ui-tui/packages/hermes-ink/src`, 해당 렌더러의 `index.d.ts`·`ambient.d.ts`, `apps/shared/src`

`vendor/`는 해당 커밋의 Git archive를 추출했다. 런타임 소스·주석·라이선스를 보존한다.
실행하지 않는 원본 테스트는 제외했다. Perdix 검증에 사용하는 테마·브랜딩·슬래시 명령
회귀 테스트 3개와 자체 런처 테스트는 유지한다.
UI 패치: `ui-tui/src/components/branding.tsx`에서 Perdix 연결일 때 공급자 이름을
표시하고 ACP가 제공하지 않는 도구·스킬 목록의 0개 표시는 서버 관리 안내로 대체한다.
Perdix 테마에서는 시작 문구도 Perdix로 표시한다. 글자 로고와 날개를 펼친 금빛 독수리 문양은 CLI의
`src/hermes/branding.ts`에서 기존 skin 계약으로 전달한다.
새 문양은 사용자가 제공한 `HERMES_CADUCEUS`의 점자 표현과 금빛 그라데이션을 따른다.
원본 `banner.ts`는 ANSI 색상 코드를 제외한 폭으로 로고를 측정한다.
`ui-tui/src/app/createSlashHandler.ts`는 Perdix 연결일 때 도움말·사용량·저장 안내·중지 명령을
Perdix 변환 계층에 전달한다. 해당 회귀 테스트는 원본 `createSlashHandler.test.ts`에 추가한다.
같은 분기에서 로그인·로그아웃·인증 상태를 처리한다. Perdix가 추가한
`ui-tui/src/app/perdixAuth.ts`는 원본 Ink의 터미널 일시 중단 API를 사용해 인증 동안
터미널을 기존 인증 프로그램에 넘기고 완료 후 화면을 복원한다.
Perdix가 작성한 코드는 `src/launch.ts`, `scripts/build.mjs` 및 CLI의 `src/hermes/`에 둔다.
원본 UI는 별도 프로세스에서 구동하고 Hermes Python 게이트웨이를 실행하지 않는다.
원본 UI의 타입 검사도 수행한다. 원본 테마·브랜딩 테스트는 그대로 실행하며,
Perdix 연결의 테스트·린트는 자체 코드에서 수행한다.

`ui-tui/src/perdixFeatures.ts`는 미연결 기능의 기본 플래그를 `false`로 유지한다.
Perdix 실행기의 `HERMES_TUI_PERDIX=1`에서 명령·단축키·하위 메뉴·RPC 요청을 제한한다.
명령 처리, 모델 선택, 세션 선택, 화면 구성, 자동완성, 요청 전송과 상태 폴링에
플래그 검사를 추가했으며 원본 Hermes 실행의 동작과 구현은 보존한다.
