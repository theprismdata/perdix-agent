#!/usr/bin/env bash
# Hermes Desktop을 Perdix 아이콘으로 띄우는 실행 껍데기를 만든다(ADR 0036: Hermes 앱 파일은 고치지 않는다).
#
# Hermes는 개발 실행에서 Dock·창 아이콘을 "앱 폴더/assets/icon-mac.png"(창은 icon.png)에서 읽고,
# 그 밖에는 "앱 폴더/dist"만 쓴다. 그래서 Hermes 앱 폴더(apps/desktop) 옆에 apps/perdix를 만들고
#   - package.json: Hermes 것을 복사하되 이름은 perdix-desktop-shell(npm 작업 공간 충돌 방지)
#   - dist, node_modules: Hermes 것을 심볼릭 링크
#   - assets: 이 저장소의 Perdix 아이콘
# 로 채운다. 앱 폴더가 같은 깊이(apps/*)라 Hermes가 계산하는 소스 루트(앱 폴더/../..)도 그대로다.
#
# 사용: integrations/hermes-desktop/install-shell.sh <Hermes 저장소>/apps/desktop
# 실행: cd <Hermes 저장소>/apps/perdix && ./node_modules/.bin/electron .
set -euo pipefail

desktop="${1:?Hermes apps/desktop 경로를 주세요}"
desktop="$(cd "$desktop" && pwd)"
[ -f "$desktop/package.json" ] && [ -d "$desktop/dist" ] || { echo "Hermes 데스크톱 앱 폴더가 아닙니다(빌드 필요): $desktop" >&2; exit 1; }

here="$(cd "$(dirname "$0")" && pwd)"
shell="$(dirname "$desktop")/perdix"
mkdir -p "$shell/assets"
# Hermes 저장소는 apps/*를 npm 작업 공간으로 읽는다. 이름이 hermes로 겹치면 npm 명령이 실패하므로
# 패키지 이름만 바꾸고 작업 공간 의존성(devDependencies 등)은 지운다. main·productName·version은 그대로 둔다.
node -e '
  const fs = require("fs");
  const pkg = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
  pkg.name = "perdix-desktop-shell";
  pkg.private = true;
  delete pkg.dependencies; delete pkg.devDependencies; delete pkg.optionalDependencies; delete pkg.scripts;
  fs.writeFileSync(process.argv[2], JSON.stringify(pkg, null, 2) + "\n");
' "$desktop/package.json" "$shell/package.json"
ln -sfn "$desktop/dist" "$shell/dist"
ln -sfn "$desktop/node_modules" "$shell/node_modules"
cp "$here/assets/icon-mac.png" "$here/assets/icon.png" "$shell/assets/"
echo "Perdix 실행 껍데기: $shell"
