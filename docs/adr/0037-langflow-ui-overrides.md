# ADR 0037: Langflow 화면 덮어쓰기 (프런트엔드만)

상태: 채택

## 배경

Hermes Desktop 안에 Langflow 편집 화면을 넣었더니(ADR 0036) GitHub·Discord 별/인원 표시, `Get started`
안내, 커뮤니티 링크 같은 홍보 요소가 이질적이었다. 처음에는 플러그인이 화면에 스크립트를 주입해 숨겼으나
DOM 추측에 기대는 방식이라 취약하고, 숨길 뿐 외부 조회 요청은 막지 못한다.

## 선택지

1. 지금처럼 PyPI Langflow를 그대로 쓰고 화면에서 숨김(취약, 외부 요청 유지).
2. **프런트엔드만 덮어쓴 빌드**를 `--frontend-path`로 서빙(채택).
3. Langflow 소스를 저장소에 포크·통합(거절: 77MB 백엔드·50MB 프런트·잦은 릴리스를 계속 따라가야 하고
   "Langflow가 업데이트되면 바로 쓴다"는 요구, 경계 규칙과 충돌).
4. `lfx`를 라이브러리로 내장(거절: 편집 화면에는 어차피 서버가 필요하고 내부 API에 의존).

## 결정

- 저장소에는 `packages/langflow-ui/overrides/`의 덮어쓰기 파일 4개와 `build.sh`, 고정 버전 파일만 둔다.
  Langflow 소스는 넣지 않고 고정한 태그를 따로 받아 빌드한다.
- 덮어쓰는 곳은 Langflow가 이런 용도로 둔 `src/frontend/src/customization/` 확장점이다(소스의 주석이
  "커뮤니티를 홍보하지 않는 배포판은 이 자리를 아무것도 그리지 않는 컴포넌트로 바꾼다"고 안내한다):
  `custom-langflow-counts`, `custom-account-menu-community-links`, `custom-get-started-progress`,
  `custom-empty-page`(빈 화면의 GitHub·Discord 카드 제거).
- 백엔드는 PyPI의 같은 버전을 그대로 쓴다. 화면과 백엔드 버전이 어긋나지 않게 `build.sh`가 받아 둔 태그가
  고정 버전과 다르면 멈춘다.
- 홍보 요소를 그리지 않으면 별/인원 조회(`api.github.com`, `discord.com`)도 일어나지 않는다
  (`use-community-counts`가 마운트될 때만 조회한다). 이 점이 플러그인 숨김 방식과의 차이다.
- Hermes 플러그인의 숨김 스크립트는 기본 Langflow 화면을 쓰는 경우를 위한 보조로 남긴다.

## 비용과 위험

- Langflow를 올릴 때마다 프런트엔드를 다시 빌드한다(Node 설치, 수 분).
- 덮어쓰는 파일이 가져오는 내부 모듈(`@/components/...`, `@/hooks/...`)이 바뀌면 빌드가 실패한다.
  그 경우 `overrides/`를 고친다. 빌드 실패는 조용히 어긋나지 않고 바로 드러난다.
- 빈 화면 덮어쓰기는 원본(`pages/MainPage/pages/empty-page.tsx`)을 복제한 것이라 원본이 바뀌면 달라질 수 있다.
