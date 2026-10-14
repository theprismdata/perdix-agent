"""로컬 프로세스 사이의 팀 상태와 append-only 메시지함."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, assert_never

import aiosqlite

from perdix.teams.models import SCHEMA, TeamCommand, TeamError, TeamMessage


class TeamStore:
    """한 프로젝트·팀·구성원·ACP 세션에 묶인 메시지함."""

    def __init__(
        self, path: Path, project: Path, team: str, member: str, session: str,
        role: Literal["leader", "member"],
    ) -> None:
        self.path = path
        self.project = str(project.resolve())
        self.team_name = team
        self.team = hashlib.sha256(f"{self.project}\0{team}".encode()).hexdigest()
        self.member = member
        self.session = session
        self.role = role

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path, timeout=10) as db:
            db.row_factory = aiosqlite.Row
            await db.executescript(SCHEMA)
            await db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                await db.commit()
            finally:
                if db.in_transaction:
                    await db.rollback()

    async def event(self, db: aiosqlite.Connection, kind: str, payload: str) -> None:
        await db.execute(
            "INSERT INTO team_events(team,actor,kind,payload,created_at) VALUES(?,?,?,?,?)",
            (self.team, self.member, kind, payload, time.time()),
        )

    async def join(self) -> None:
        async with self.transaction() as db:
            async with db.execute("SELECT stopped FROM teams WHERE id=?", (self.team,)) as c:
                team = await c.fetchone()
            if team is None:
                if self.role != "leader":
                    raise TeamError("리더 터미널을 먼저 시작하세요")
                await db.execute("INSERT INTO teams(id,name,project) VALUES(?,?,?)",
                                 (self.team, self.team_name, self.project))
            elif team["stopped"]:
                raise TeamError("종료된 팀입니다. 새 팀 이름을 사용하세요")
            async with db.execute(
                "SELECT name,session,role,active FROM members WHERE team=?", (self.team,),
            ) as c:
                members = await c.fetchall()
            for member in members:
                if member["active"] and member["name"] == self.member:
                    if member["session"] == self.session and member["role"] == self.role:
                        return
                    raise TeamError("이미 사용 중인 구성원 이름입니다")
                if member["active"] and member["role"] == "leader" and self.role == "leader":
                    raise TeamError("팀에는 리더가 한 명만 참여할 수 있습니다")
            await db.execute(
                "INSERT INTO members(team,name,session,role) VALUES(?,?,?,?) "
                "ON CONFLICT(team,name) DO UPDATE SET session=excluded.session,"
                "role=excluded.role,active=1", (self.team, self.member, self.session, self.role),
            )
            await self.event(db, "join", self.session)
            if self.role == "member":
                for leader in members:
                    if leader["active"] and leader["role"] == "leader":
                        await self.deliver(db, leader["name"],
                                           f"[팀 참여] {self.member} 구성원이 참여했습니다")

    async def check(self, db: aiosqlite.Connection, *, allow_stopped: bool = False) -> None:
        async with db.execute(
            "SELECT m.active,t.stopped FROM members m JOIN teams t ON t.id=m.team "
            "WHERE m.team=? AND m.name=? AND m.session=?",
            (self.team, self.member, self.session),
        ) as c:
            row = await c.fetchone()
        if row is None or not row["active"] or (row["stopped"] and not allow_stopped):
            raise TeamError("팀 참여가 종료되었습니다")

    async def stopped(self) -> bool:
        async with self.transaction() as db:
            async with db.execute("SELECT stopped FROM teams WHERE id=?", (self.team,)) as c:
                row = await c.fetchone()
        return row is None or bool(row["stopped"])

    async def deliver(self, db: aiosqlite.Connection, recipient: str, body: str) -> None:
        async with db.execute(
            "SELECT session FROM members WHERE team=? AND name=? AND active=1",
            (self.team, recipient),
        ) as c:
            member = await c.fetchone()
        if member is None:
            raise TeamError(f"참여 중인 구성원이 없습니다: {recipient}")
        async with db.execute(
            "SELECT COUNT(*) FROM messages WHERE team=? AND recipient=? "
            "AND id NOT IN (SELECT message FROM receipts WHERE session=?)",
            (self.team, member["session"], member["session"]),
        ) as c:
            count = await c.fetchone()
        if count is not None and count[0] >= 100:
            raise TeamError("수신자의 메시지함이 가득 찼습니다")
        # 이름 재사용 시 이전 세션의 메시지를 새 구성원에게 보내지 않는다.
        await db.execute("INSERT INTO messages(team,sender,recipient,body) VALUES(?,?,?,?)",
                         (self.team, self.member, member["session"], body))

    async def pending(self, *, allow_stopped: bool = False) -> list[TeamMessage]:
        async with self.transaction() as db:
            await self.check(db, allow_stopped=allow_stopped)
            async with db.execute(
                "SELECT id,sender,body FROM messages WHERE team=? AND recipient=? "
                "AND id NOT IN (SELECT message FROM receipts WHERE session=?) "
                "ORDER BY id LIMIT 100",
                (self.team, self.session, self.session),
            ) as c:
                rows = await c.fetchall()
            return [TeamMessage(id=r["id"], sender=r["sender"], body=r["body"]) for r in rows]

    async def acknowledge(self, messages: list[TeamMessage]) -> None:
        async with self.transaction() as db:
            await db.executemany("INSERT OR IGNORE INTO receipts(message,session) VALUES(?,?)",
                                 [(m.id, self.session) for m in messages])

    async def command(self, command: TeamCommand) -> str:
        async with self.transaction() as db:
            await self.check(db, allow_stopped=command.action == "status")
            match command.action:
                case "status":
                    async with db.execute(
                        "SELECT name,role,active FROM members WHERE team=?", (self.team,),
                    ) as c:
                        members = [dict(r) for r in await c.fetchall()]
                    async with db.execute(
                        "SELECT id,owner,body,paths,state,result FROM tasks WHERE team=?",
                        (self.team,),
                    ) as c:
                        tasks = [dict(r) for r in await c.fetchall()]
                    return json.dumps({"members": members, "tasks": tasks}, ensure_ascii=False)
                case "send":
                    if not command.recipient or not command.body.strip():
                        raise TeamError("recipient과 비어 있지 않은 body가 필요합니다")
                    await self.deliver(db, command.recipient, command.body)
                case "broadcast":
                    if not command.body.strip():
                        raise TeamError("비어 있지 않은 body가 필요합니다")
                    async with db.execute(
                        "SELECT name FROM members WHERE team=? AND active=1 AND name!=?",
                        (self.team, self.member),
                    ) as c:
                        recipients = await c.fetchall()
                    for recipient in recipients:
                        await self.deliver(db, recipient["name"], command.body)
                case "assign":
                    await self.assign(db, command)
                case "complete" | "block":
                    state = "done" if command.action == "complete" else "blocked"
                    cursor = await db.execute(
                        "UPDATE tasks SET state=?,result=? WHERE team=? AND id=? AND owner=? "
                        "AND state IN ('active','blocked')",
                        (state, command.body, self.team, command.task_id, self.member),
                    )
                    if cursor.rowcount != 1:
                        raise TeamError("자신에게 배정된 미완료 작업만 변경할 수 있습니다")
                    async with db.execute(
                        "SELECT name FROM members WHERE team=? AND role='leader' AND active=1",
                        (self.team,),
                    ) as c:
                        leader = await c.fetchone()
                    if leader is not None:
                        await self.deliver(db, leader["name"],
                                           f"작업 {command.task_id}: {state}\n{command.body}")
                case "remove" | "stop":
                    self.require_leader()
                    if command.action == "stop":
                        await db.execute("UPDATE teams SET stopped=1 WHERE id=?", (self.team,))
                    else:
                        if not command.recipient or command.recipient == self.member:
                            raise TeamError("제거할 다른 구성원을 지정하세요")
                        await db.execute("UPDATE members SET active=0 WHERE team=? AND name=?",
                                         (self.team, command.recipient))
                        await db.execute(
                            "UPDATE tasks SET state='blocked' WHERE team=? AND owner=? "
                            "AND state='active'", (self.team, command.recipient),
                        )
                case unreachable:
                    assert_never(unreachable)
            await self.event(db, command.action, command.model_dump_json())
            return "팀 명령을 처리했습니다"

    def require_leader(self) -> None:
        if self.role != "leader":
            raise TeamError("리더만 실행할 수 있습니다")

    async def assign(self, db: aiosqlite.Connection, command: TeamCommand) -> None:
        self.require_leader()
        if not command.task_id or not command.recipient or not command.body.strip():
            raise TeamError("task_id, recipient, body가 필요합니다")
        paths = [str(Path(p)) for p in command.paths]
        if any(Path(p).is_absolute() or ".." in Path(p).parts or p == "." for p in paths):
            raise TeamError("paths에는 프로젝트 내 상대 파일·디렉터리를 지정하세요")
        async with db.execute(
            "SELECT id,paths FROM tasks WHERE team=? AND state='active' AND id!=?",
            (self.team, command.task_id),
        ) as c:
            tasks = await c.fetchall()
        for task in tasks:
            occupied: list[str] = json.loads(task["paths"])
            if any(a == b or a.startswith(b + "/") or b.startswith(a + "/")
                   for a in paths for b in occupied):
                raise TeamError(f"진행 중인 작업과 파일 범위가 겹칩니다: {task['id']}")
        await self.deliver(db, command.recipient,
                           f"[작업 {command.task_id}]\n{command.body}\n담당 범위: {paths}")
        await db.execute(
            "INSERT INTO tasks(team,id,owner,body,paths,state) VALUES(?,?,?,?,?,'active') "
            "ON CONFLICT(team,id) DO UPDATE SET owner=excluded.owner,body=excluded.body,"
            "paths=excluded.paths,state='active',result=''",
            (self.team, command.task_id, command.recipient, command.body, json.dumps(paths)),
        )

    async def leave(self) -> None:
        async with self.transaction() as db:
            cursor = await db.execute(
                "UPDATE members SET active=0 WHERE team=? AND name=? AND session=? AND active=1",
                (self.team, self.member, self.session),
            )
            if cursor.rowcount:
                await db.execute(
                    "UPDATE tasks SET state='blocked' WHERE team=? AND owner=? AND state='active'",
                    (self.team, self.member),
                )
                await self.event(db, "leave", self.session)
