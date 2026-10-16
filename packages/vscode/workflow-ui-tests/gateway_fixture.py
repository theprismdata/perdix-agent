"""실제 Gateway/ACP 브라우저 검증용 격리된 서버와 Git 저장소."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from pathlib import Path

import uvicorn
from perdix.config.schema import ServerConfig
from perdix.gateway.app import create_app
from perdix.gateway.config import GatewayConfig, GatewaySecrets, ProjectConfig


def prepare(root: Path) -> None:
    repo = root / "repo"
    repo.mkdir(parents=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.name", "Workflow Browser"],
        ["config", "user.email", "browser@example.invalid"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    config = {
        "verify": {
            "commands": [
                {
                    "name": "두 변경 검사",
                    "run": 'test "$(cat a.txt)" = good && test "$(cat b.txt)" = good',
                }
            ]
        }
    }
    (repo / ".perdix").mkdir()
    (repo / ".perdix/config.json").write_text(json.dumps(config))
    subprocess.run(
        ["git", "-C", str(repo), "add", "."], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "base"],
        check=True,
        capture_output=True,
    )
    worker = root / "write_candidate.py"
    worker.write_text("""import pathlib, sys, time
root = pathlib.Path(sys.argv[1])
task = pathlib.Path.cwd().parents[1].name
counter = root / (task + '.count')
round_number = int(counter.read_text()) + 1 if counter.exists() else 1
pathlib.Path(task + '.txt').write_text('bad' if round_number == 1 else 'good')
counter.write_text(str(round_number))
deadline = time.monotonic() + 20
while not all((root / (name + '.count')).exists() and int((root / (name + '.count')).read_text()) >= round_number for name in ['a', 'b']):
    if time.monotonic() > deadline: raise RuntimeError('parallel barrier timed out')
    time.sleep(.05)
print(task, round_number)
""")
    responses: list[dict[str, object]] = [{"text": "분석 완료"}]
    import shlex

    for round_number in (1, 2):
        for task in ("a", "b"):
            responses.append(
                {
                    "tool_uses": [
                        {
                            "id": f"write-{round_number}-{task}",
                            "name": "bash",
                            "input": {
                                "command": " ".join(
                                    map(
                                        shlex.quote,
                                        [sys.executable, str(worker), str(root)],
                                    )
                                )
                            },
                        }
                    ]
                }
            )
        responses.extend([{"text": "구현 완료"}, {"text": "구현 완료"}])
    responses.append({"text": '{"repair_tasks":[]}'})
    (root / "fake.json").write_text(json.dumps(responses))
    tasks = [
        {
            "taskId": "analysis",
            "title": "변경 분석",
            "role": "analyze",
            "model": "fake-model",
            "paths": ["."],
            "acceptance": ["작업 범위 확인"],
        }
    ]
    tasks += [
        {
            "taskId": key,
            "title": f"{key} 파일 구현",
            "role": "implement",
            "model": "fake-model",
            "paths": [f"{key}.txt"],
            "acceptance": ["파일 내용 good"],
            "dependencies": ["analysis"],
        }
        for key in ("a", "b")
    ]
    (root / "plan.json").write_text(
        json.dumps({"objective": "두 독립 파일을 검증하고 수락", "tasks": tasks})
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    root = args.root.resolve()
    if not (root / "repo").exists():
        prepare(root)
    config = GatewayConfig(
        db_path=root / "gateway.db",
        projects={"browser": ProjectConfig(cwd=root / "repo", trust_project=True)},
        server=ServerConfig(
            provider="fake",
            model="fake-model",
            fake_script=root / "fake.json",
            db_path=str(root / "sessions.db"),
            credentials_path=root / "credentials.json",
            team_db=root / "teams.sqlite3",
        ),
    )
    app = create_app(config, GatewaySecrets(gateway="workflow-browser-fixture-token"))
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]
    print(json.dumps({"port": port, "root": str(root)}), flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])


if __name__ == "__main__":
    main()
