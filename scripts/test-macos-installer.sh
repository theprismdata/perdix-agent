#!/usr/bin/env bash
set -euo pipefail
app="$1"
test_root="$(mktemp -d /tmp/perdix-macos-test-XXXXXX)"
trap 'rm -rf "$test_root"' EXIT
installer="$app/Contents/MacOS/PerdixSetup"
payload="$app/Contents/Resources/payload"
destination="$test_root/install with spaces"
config="$test_root/config"
user_home="$test_root/home with spaces"
printf 'test-key\n' | "$installer" --test-install "$payload" "$destination" "$config" "$user_home" openai gpt-6.1-sol
"$user_home/.local/bin/perdix" --version
"$user_home/.local/bin/perdix-server" --version
printf '\n' | "$installer" --test-install "$payload" "$destination" "$config" "$user_home" anthropic claude-opus-5-5
uv run --project server python -c '
import json, pathlib, stat, sys
config, destination, home = map(pathlib.Path, sys.argv[1:])
assert json.loads((config / "credentials.json").read_text())["openai"]["api_key"] == "test-key"
assert json.loads((config / "preferences.json").read_text())["default_model"] == "claude-opus-5-5"
assert stat.S_IMODE((config / "credentials.json").stat().st_mode) == 0o600
assert len(list((destination / "versions").iterdir())) == 2
assert not (home / ".zprofile").exists()
assert not (home / "Applications").exists()
print("PASS: install, reinstall, paths with spaces, preserved key, permissions, isolated integrations")
' "$config" "$destination" "$user_home"
pnpm --filter perdix-vscode test:server "$user_home/.local/bin/perdix-server"
uv run --project server python -c '
import json, pathlib, subprocess, sys
root, home, config = map(pathlib.Path, sys.argv[1:])
script = root / "fake.json"
script.write_text(json.dumps([{"text": "Native CLI reply"}]))
server = root / "server"
server.symlink_to(home / ".local/bin/perdix-server")
database = root / "test.db"
credentials = config / "empty.json"
credentials.write_text("{}")
command = f"{server} --provider fake --model fake-model --fake-script {script} --db {database} --credentials {credentials}"
result = subprocess.run([str(home / ".local/bin/perdix"), "--server", command, "--model", "fake-model", "--cwd", str(root), "-p", "hello", "--json"], text=True, capture_output=True, timeout=30)
assert result.returncode == 0, result.stderr
assert "Native CLI reply" in result.stdout, result.stdout
print("PASS: native CLI streamed fake-provider prompt through bundled server")
' "$test_root" "$user_home" "$config"
