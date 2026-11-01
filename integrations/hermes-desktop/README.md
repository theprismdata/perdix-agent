# Hermes Desktop용 Perdix 실행 껍데기

Hermes Desktop을 Perdix 아이콘으로 띄운다. Hermes 앱 파일은 고치지 않는다([ADR 0036](../../docs/adr/0036-hermes-desktop-compat-server.md)).

## 원리

Hermes는 개발 실행에서 Dock과 창 아이콘을 **앱 폴더의 `assets/icon-mac.png`**(창은 `icon.png`)에서 읽는다.
앱 폴더에서 그 밖에 읽는 것은 `dist`뿐이다. 그래서 Hermes 앱 폴더(`apps/desktop`) 옆에 `apps/perdix`를 만든다.

| 항목 | 내용 |
| --- | --- |
| `package.json` | Hermes 것을 복사. 이름만 `perdix-desktop-shell`로 바꾸고 의존성 목록은 뺀다(Hermes 저장소의 npm 작업 공간과 이름이 겹치지 않게) |
| `dist`, `node_modules` | Hermes 것을 가리키는 심볼릭 링크 |
| `assets/icon-mac.png`, `assets/icon.png` | 이 폴더의 Perdix 아이콘 |

같은 깊이(`apps/*`)라 Hermes가 계산하는 소스 루트(앱 폴더의 두 단계 위)도 바뀌지 않는다.

## 만들기와 실행

Hermes를 소스에서 빌드한 뒤(`apps/desktop`에서 `npm install`, `npm run build`):

```bash
integrations/hermes-desktop/install-shell.sh <Hermes 저장소>/apps/desktop
cd <Hermes 저장소>/apps/perdix && ./node_modules/.bin/electron .
```

원격 모드 환경 변수(`HERMES_DESKTOP_REMOTE_URL` 등)는 [docs/hermes-desktop.md](../../docs/hermes-desktop.md)와 같다.
Hermes를 다시 빌드해도 링크라서 껍데기를 다시 만들 필요는 없다. 아이콘을 바꿨을 때만 다시 실행한다.

## 아이콘

`assets/icon.svg`가 원본이다. 노드와 네온 선으로 이은 자고새(perdix) 별자리, 곧 워크플로 그래프로 그린 새다. PNG는 다음으로 다시 만든다.

```bash
rsvg-convert -w 1024 -h 1024 assets/icon.svg -o assets/icon-mac.png && cp assets/icon-mac.png assets/icon.png
```

## 한계

- 메뉴 막대의 앱 이름은 아직 바뀌지 않는다(개발 실행이라 `Electron`으로 보인다).
- 앱 안의 Hermes 로고와 시작 화면 그림은 Hermes `dist`에 있어 그대로다.
- 패키징한 `.app`은 번들 아이콘을 쓰므로 이 방법이 적용되지 않는다.
- 이 저장소 밖(VS Code 같은 Electron 앱 안)에서 실행 스크립트를 부르면 `ELECTRON_RUN_AS_NODE=1`이 물려
  `bad option` 오류로 뜨지 않는다. 그때는 `env -u ELECTRON_RUN_AS_NODE`로 실행한다.
