#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
root="$PWD"
case "$(uname -m)" in arm64) arch=arm64; swift_arch=arm64;; x86_64) arch=x64; swift_arch=x86_64;; *) exit 1;; esac
mkdir -p "$root/dist"
work="$(mktemp -d "$root/dist/.macos-build-XXXXXX")"
mkdir -p "$work/dmg"
app="$work/dmg/Perdix Setup.app"
payload="$app/Contents/Resources/payload"
mkdir -p "$payload" "$app/Contents/MacOS" "$root/dist/release"
printf '빌드 작업 경로: %s\n' "$work"
pnpm install --frozen-lockfile
pnpm --filter @perdix/protocol build
bun build packages/cli/src/main.ts --compile --target="bun-darwin-$arch" --define 'process.env.DEV=false' --define 'process.env.NODE_ENV="production"' --outfile "$payload/perdix"
pnpm --filter perdix-vscode build
pnpm --filter perdix-vscode exec vsce package --no-dependencies --allow-missing-repository --out "$payload/perdix.vsix"
PYTHONDONTWRITEBYTECODE=1 uv run --project server --locked --python 3.12 --with pyinstaller==6.22.3 pyinstaller --noconfirm --clean --onedir --name perdix-server --distpath "$work/python-dist" --workpath "$work/python-build" --specpath "$work" --paths "$root/server/src" --collect-all acp --collect-all anthropic --collect-all openai --collect-all jsonschema_specifications "$root/server/scripts/frozen_entry.py"
mv "$work/python-dist/perdix-server" "$payload/server"
swiftc scripts/macos/Installer.swift -target "$swift_arch-apple-macosx13.0" -framework AppKit -o "$app/Contents/MacOS/PerdixSetup"
cp scripts/macos/Info.plist "$app/Contents/Info.plist"
cp docs/macos-installation.md "$work/dmg/README.md"
codesign --force --deep --sign - "$app"
"$payload/perdix" --version
"$payload/server/perdix-server" --version
hdiutil create -volname 'Perdix Setup' -srcfolder "$work/dmg" -format UDZO -ov "$root/dist/release/Perdix-Setup-macos-$arch.dmg"
printf '완료: dist/release/Perdix-Setup-macos-%s.dmg\n' "$arch"
mv "$work" "$HOME/.Trash/perdix-macos-build-$(date +%s)-$$"
