# macOS 설치

Apple Silicon Mac은 `Perdix-Setup-macos-arm64.dmg`, Intel Mac은 x64 빌드를 사용합니다.
macOS 13 이상에서 DMG를 열고 **Perdix Setup.app**을 실행하세요. 공급자·API 키를
선택하면 CLI·Python 서버와 설치되어 있는 VS Code의 확장을 준비합니다. Node.js·Python·uv
사전 설치는 필요하지 않습니다. 키를 비우면 기존 키나 환경변수를 사용합니다.

새 터미널에서 `perdix`를 실행합니다. Finder의 사용자 `Applications/Perdix.command`로도
열 수 있습니다. VS Code는 `Developer: Reload Window` 후 Perdix 화면을 여세요.
API 사용 요금은 선택한 공급자 계정에 부과됩니다.

서버와 CLI는 사용자 `Library/Application Support/Perdix/versions`에 설치합니다.
`.local/bin`의 실행 링크와 `.zprofile`의 PATH를 설정합니다. `.perdix/credentials.json`의
API 키는 사용자별로 저장하고 모델은 `.perdix/agents.json`의 역할별 할당을 사용합니다. 이전 설치와
세션은 삭제하지 않습니다. VS Code가 없으면 설치된 VSIX를 나중에 수동 설치할 수 있습니다.

현재 DMG는 개발용 ad-hoc 서명이며 Apple 공증을 받은 설치본이 아닙니다. 다운로드한 앱이
차단되면 시스템 설정의 개인정보 보호 및 보안에서 설치 앱 실행을 허용해야 할 수 있습니다.

빌드: 해당 아키텍처의 Mac에서 `pnpm build:macos`. 개발 환경에는 Node.js 22+, pnpm,
Bun, uv와 Xcode Command Line Tools가 필요합니다. Intel 바이너리는 Intel 환경에서 별도
빌드해야 하며 Apple Silicon 빌드로 Intel 실행을 보장하지 않습니다.
