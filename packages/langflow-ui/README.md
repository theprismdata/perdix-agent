# Langflow 화면 (Perdix 판)

Langflow의 편집 화면에서 커뮤니티 홍보 요소(헤더의 GitHub·Discord 별/인원, `Get started` 안내,
프로필 메뉴의 커뮤니티 링크, 빈 화면의 GitHub·Discord 카드)를 뺀 프런트엔드를 빌드한다.
설계: [ADR 0037](../../docs/adr/0037-langflow-ui-overrides.md).

- Langflow 소스는 저장소에 넣지 않는다. `build.sh`가 고정한 버전(`LANGFLOW_VERSION`)의 태그를 따로
  받아 `overrides/`의 파일만 덮어쓰고 프런트엔드만 빌드한다. 백엔드는 PyPI의 같은 버전을 그대로 쓴다.
- 덮어쓰는 곳은 Langflow가 이런 용도로 둔 `src/frontend/src/customization/` 확장점뿐이다.
- 이 표시들을 그리지 않으면 Langflow가 `api.github.com`·`discord.com`을 조회하는 요청도 일어나지 않는다.

## 빌드와 실행

```bash
packages/langflow-ui/build.sh            # ~/.perdix/langflow-ui/build 생성
langflow run --frontend-path ~/.perdix/langflow-ui/build
```

환경변수: `LANGFLOW_SOURCE`(저장소 주소 또는 로컬 경로, 기본 GitHub), `PERDIX_LANGFLOW_UI_WORK`(작업 폴더).
Node 20.19 이상과 git이 필요하고 첫 빌드에 의존성 설치(수 분)가 든다.

## Langflow를 올릴 때

1. 백엔드를 새 버전으로 올린다(`pip install langflow==X`).
2. `LANGFLOW_VERSION`을 같은 버전으로 바꾸고 `build.sh`를 다시 실행한다. 화면과 백엔드 버전이 다르면
   API가 어긋날 수 있어 스크립트가 받아 둔 태그와 `LANGFLOW_VERSION`이 다르면 멈춘다.
3. 덮어쓰는 파일이 가져오는 내부 모듈(`@/...`)이 바뀌면 빌드가 실패한다. 그때는 `overrides/`를 고친다.

## 알아둘 점

- Hermes 앱처럼 Langflow 화면을 오래 열어 두는 곳에서는, 화면을 바꾼 뒤 이미 열려 있던 페이지가 이전 문서를
  유지한다. 앱을 다시 시작하면 새 화면을 받는다(번들 파일 이름이 빌드마다 달라 캐시가 섞이지는 않는다).
- Hermes 플러그인의 숨김 스크립트(`packages/cli/src/hermes/langflow-plugin.ts`)는 기본 Langflow 화면을
  쓰는 경우를 위한 보조라, 이 빌드를 쓰면 할 일이 없다.
