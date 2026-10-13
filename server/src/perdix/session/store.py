"""SQLite event log (append-only) + FTS5 검색.

세션 기록은 `events` 테이블에 순서 번호(`seq`, 세션별 1부터 빈틈 없이 증가)와 함께
append만 한다. 과거 레코드는 수정·삭제하지 않는다(AGENTS.md 규칙 4, 8).

이벤트 kind별 JSON payload 형식
--------------------------------
`user_message`
    `UserMessage` 덤프.
    ``{"role": "user", "content": [{"type": "text", "text": str}
    | {"type": "image", "media_type": str, "data": str(base64)}], "origin": "user"|"synthetic"}``
`assistant_message`
    `AssistantMessage` 덤프. 스트림이 정상 종료했을 때만 커밋한다.
    ``{"role": "assistant", "items": [{"kind": "text", "text": str}
    | {"kind": "tool_use", "id": str, "name": str, "input": {...}}
    | {"kind": "thinking"|"reasoning"|"signature", "provider": str, "model": str, "raw": {...}}],
    "provider": str, "model": str, "stop_reason": str}``
`tool_started`
    `ToolStartedPayload`. 도구 실행 시작 직전에 커밋.
    ``{"tool_use_id": str, "name": str, "index": int}``
`tool_result`
    `ToolResult` 덤프. 완료 즉시 커밋(완료 순서). 투영이 `index`로 재정렬한다.
    ``{"tool_use_id": str, "index": int, "content": str, "is_error": bool,
    "display": [{"type": "text", "text": str}
    | {"type": "diff", "path": str, "old_text": str|null, "new_text": str}]}``
`permission_decision`
    `PermissionDecisionPayload`.
    ``{"tool_use_id": str, "tool_name": str, "decision": str, ...}`` (추가 키 허용)
`mode_change`
    `ModeChangePayload`. ``{"mode": str}``
`config_change`
    `ConfigChangePayload`. ``{"key": str, "value": any}``
`compaction`
    `CompactionPayload`. ``[from_seq, to_seq]`` 범위(양 끝 포함)의 메시지를
    투영에서 요약 하나로 치환한다.
    ``{"from_seq": int, "to_seq": int, "summary": str, "provider": str, "model": str,
    "opaque": {...}|null}``

FTS 색인 대상은 `user_message`, `assistant_message`(텍스트 아이템), `compaction`(summary)이다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Final, Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from perdix.context.messages import AssistantMessage, ToolResult, UserMessage
from perdix.harness.goal_state import LoopState

SCHEMA_VERSION: Final = 1
DEFAULT_DB_PATH: Final = Path.home() / ".perdix" / "perdix.db"

EventKind = Literal[
    "user_message",
    "assistant_message",
    "tool_started",
    "tool_result",
    "permission_decision",
    "mode_change",
    "config_change",
    "compaction",
    "loop_state",
]
EVENT_KINDS: Final[frozenset[str]] = frozenset(
    {
        "user_message",
        "assistant_message",
        "tool_started",
        "tool_result",
        "permission_decision",
        "mode_change",
        "config_change",
        "compaction",
        "loop_state",
    }
)


class SchemaTooNewError(RuntimeError):
    """DB의 schema_version이 이 서버가 아는 것보다 새로워 쓰기를 거부한다."""


# ---------------------------------------------------------------- payload 모델


class ToolStartedPayload(BaseModel):
    tool_use_id: str
    name: str
    index: int


class PermissionDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    tool_use_id: str
    tool_name: str
    decision: str


class ModeChangePayload(BaseModel):
    mode: str


class ConfigChangePayload(BaseModel):
    key: str
    value: Any


class CompactionPayload(BaseModel):
    from_seq: int
    to_seq: int
    summary: str
    provider: str
    model: str
    opaque: dict[str, Any] | None = None


_PAYLOAD_MODELS: Final[dict[str, type[BaseModel]]] = {
    "user_message": UserMessage,
    "assistant_message": AssistantMessage,
    "tool_started": ToolStartedPayload,
    "tool_result": ToolResult,
    "permission_decision": PermissionDecisionPayload,
    "mode_change": ModeChangePayload,
    "config_change": ConfigChangePayload,
    "compaction": CompactionPayload,
    "loop_state": LoopState,
}

# ---------------------------------------------------------------- 레코드


class Event(BaseModel):
    id: int
    session_id: str
    seq: int
    kind: str
    payload: dict[str, Any]
    created_at: float

    def _expect(self, kind: str) -> None:
        if self.kind != kind:
            raise ValueError(f"event seq={self.seq} is {self.kind!r}, not {kind!r}")

    def as_user_message(self) -> UserMessage:
        self._expect("user_message")
        return UserMessage.model_validate(self.payload)

    def as_assistant_message(self) -> AssistantMessage:
        self._expect("assistant_message")
        return AssistantMessage.model_validate(self.payload)

    def as_tool_started(self) -> ToolStartedPayload:
        self._expect("tool_started")
        return ToolStartedPayload.model_validate(self.payload)

    def as_tool_result(self) -> ToolResult:
        self._expect("tool_result")
        return ToolResult.model_validate(self.payload)

    def as_permission_decision(self) -> PermissionDecisionPayload:
        self._expect("permission_decision")
        return PermissionDecisionPayload.model_validate(self.payload)

    def as_mode_change(self) -> ModeChangePayload:
        self._expect("mode_change")
        return ModeChangePayload.model_validate(self.payload)

    def as_config_change(self) -> ConfigChangePayload:
        self._expect("config_change")
        return ConfigChangePayload.model_validate(self.payload)

    def as_compaction(self) -> CompactionPayload:
        self._expect("compaction")
        return CompactionPayload.model_validate(self.payload)


class SessionRecord(BaseModel):
    id: str
    project_path: str
    project_hash: str
    mode: str
    model: str
    provider: str
    created_at: float
    updated_at: float
    title: str | None = None
    permission_rules: list[dict[str, Any]] = Field(default_factory=list)


class SearchHit(BaseModel):
    session_id: str
    project_path: str
    created_at: float
    seq: int
    kind: str
    text: str
    same_project: bool


# ---------------------------------------------------------------- 내부 함수


def project_hash(project_path: str) -> str:
    """정규화한 절대 경로의 sha256 앞 16자."""
    resolved = str(Path(project_path).expanduser().resolve())
    return hashlib.sha256(resolved.encode()).hexdigest()[:16]


def _indexable_text(kind: str, payload: dict[str, Any]) -> str | None:
    if kind == "user_message":
        text = UserMessage.model_validate(payload).text()
    elif kind == "assistant_message":
        text = AssistantMessage.model_validate(payload).text()
    elif kind == "compaction":
        text = CompactionPayload.model_validate(payload).summary
    else:
        return None
    return text or None


def _fts_query(query: str) -> str:
    """사용자 입력을 FTS5 문법 오류 없이 쓰도록 토큰마다 큰따옴표로 감싼다(AND 결합)."""
    tokens = query.split()
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


_SCHEMA_SQL: Final = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project_path TEXT NOT NULL,
    project_hash TEXT NOT NULL,
    mode TEXT NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    title TEXT,
    permission_rules TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS sessions_project ON sessions(project_hash, updated_at);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(session_id, seq)
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_events
    USING fts5(text, content='events', content_rowid='id');
"""

_UPDATABLE_FIELDS: Final = frozenset({"mode", "model", "provider", "title", "permission_rules"})
_SESSION_COLUMNS: Final = (
    "id, project_path, project_hash, mode, model, provider, created_at, updated_at, title, "
    "permission_rules"
)


def _session_from_row(row: aiosqlite.Row) -> SessionRecord:
    return SessionRecord(
        id=row[0],
        project_path=row[1],
        project_hash=row[2],
        mode=row[3],
        model=row[4],
        provider=row[5],
        created_at=row[6],
        updated_at=row[7],
        title=row[8],
        permission_rules=json.loads(row[9]),
    )


class EventStore:
    """세션 event log. 쓰기는 `BEGIN IMMEDIATE` 짧은 트랜잭션으로 직렬화한다."""

    def __init__(self, conn: aiosqlite.Connection, *, read_only: bool) -> None:
        self._conn = conn
        self._read_only = read_only
        self._write_lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: str | Path = DEFAULT_DB_PATH) -> EventStore:
        path_str = str(path)
        if path_str != ":memory:":
            Path(path_str).expanduser().parent.mkdir(parents=True, exist_ok=True)
            path_str = str(Path(path_str).expanduser())
        conn = await aiosqlite.connect(path_str, isolation_level=None)
        try:
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            read_only = await cls._init_schema(conn)
        except BaseException:
            await conn.close()
            raise
        return cls(conn, read_only=read_only)

    @staticmethod
    async def _init_schema(conn: aiosqlite.Connection) -> bool:
        """스키마를 만들거나 확인한다. DB가 더 새 버전이면 True(읽기 전용)."""
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
            ) as cur:
                has_meta = await cur.fetchone() is not None
            version: int | None = None
            if has_meta:
                async with conn.execute("SELECT value FROM meta WHERE key='schema_version'") as cur:
                    row = await cur.fetchone()
                version = int(row[0]) if row is not None else None
            if version is not None and version > SCHEMA_VERSION:
                await conn.execute("ROLLBACK")
                return True
            # executescript는 진행 중 트랜잭션을 커밋해 버리므로 문장 단위로 실행한다.
            for stmt in _SCHEMA_SQL.split(";"):
                if stmt.strip():
                    await conn.execute(stmt)
            await conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            await conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                await conn.execute("ROLLBACK")
            raise
        return False

    @property
    def read_only(self) -> bool:
        return self._read_only

    def _check_writable(self) -> None:
        if self._read_only:
            raise SchemaTooNewError(
                f"database schema is newer than supported version {SCHEMA_VERSION}; "
                "upgrade perdix to write to it"
            )

    # ------------------------------------------------------------ 세션

    async def create_session(
        self, project_path: str, mode: str, provider: str, model: str
    ) -> SessionRecord:
        self._check_writable()
        now = time.time()
        record = SessionRecord(
            id=uuid.uuid4().hex,
            project_path=project_path,
            project_hash=project_hash(project_path),
            mode=mode,
            model=model,
            provider=provider,
            created_at=now,
            updated_at=now,
        )
        async with self._write_lock:
            await self._conn.execute(
                f"INSERT INTO sessions({_SESSION_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    record.id,
                    record.project_path,
                    record.project_hash,
                    record.mode,
                    record.model,
                    record.provider,
                    record.created_at,
                    record.updated_at,
                    record.title,
                    json.dumps(record.permission_rules),
                ),
            )
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        async with self._conn.execute(
            f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
        return _session_from_row(row) if row is not None else None

    async def list_sessions(self, project_path: str | None = None) -> list[SessionRecord]:
        """최근 갱신 순. `project_path`가 주어지면 그 프로젝트만."""
        if project_path is None:
            sql = f"SELECT {_SESSION_COLUMNS} FROM sessions ORDER BY updated_at DESC"
            params: tuple[Any, ...] = ()
        else:
            sql = (
                f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE project_hash = ? "
                "ORDER BY updated_at DESC"
            )
            params = (project_hash(project_path),)
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_session_from_row(r) for r in rows]

    async def update_session(self, session_id: str, **fields: Any) -> None:
        """`mode`, `model`, `provider`, `title`, `permission_rules`만 갱신 가능."""
        self._check_writable()
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"cannot update session fields: {sorted(unknown)}")
        values: dict[str, Any] = dict(fields)
        if "permission_rules" in values:
            values["permission_rules"] = json.dumps(values["permission_rules"])
        values["updated_at"] = time.time()
        assignments = ", ".join(f"{k} = ?" for k in values)
        async with self._write_lock:
            cur = await self._conn.execute(
                f"UPDATE sessions SET {assignments} WHERE id = ?",
                (*values.values(), session_id),
            )
            if cur.rowcount == 0:
                raise KeyError(session_id)

    # ------------------------------------------------------------ 이벤트

    async def append(self, session_id: str, kind: str, payload: BaseModel | dict[str, Any]) -> int:
        """이벤트를 append하고 세션 내 `seq`를 돌려준다."""
        self._check_writable()
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind: {kind!r}")
        model_cls = _PAYLOAD_MODELS[kind]
        if isinstance(payload, BaseModel):
            if not isinstance(payload, model_cls):
                raise TypeError(f"{kind} payload must be {model_cls.__name__}")
            data = payload.model_dump(mode="json")
        else:
            data = model_cls.model_validate(payload).model_dump(mode="json")
        payload_json = json.dumps(data, ensure_ascii=False)
        text = _indexable_text(kind, data)
        now = time.time()
        async with self._write_lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            try:
                async with self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE session_id = ?",
                    (session_id,),
                ) as cur:
                    row = await cur.fetchone()
                assert row is not None
                seq = int(row[0])
                cur2 = await self._conn.execute(
                    "INSERT INTO events(session_id, seq, kind, payload, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (session_id, seq, kind, payload_json, now),
                )
                if text is not None:
                    await self._conn.execute(
                        "INSERT INTO fts_events(rowid, text) VALUES (?, ?)",
                        (cur2.lastrowid, text),
                    )
                await self._conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
                )
                await self._conn.execute("COMMIT")
            except BaseException:
                await self._conn.execute("ROLLBACK")
                raise
        return seq

    async def events(self, session_id: str) -> list[Event]:
        async with self._conn.execute(
            "SELECT id, session_id, seq, kind, payload, created_at FROM events "
            "WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Event(
                id=r[0],
                session_id=r[1],
                seq=r[2],
                kind=r[3],
                payload=json.loads(r[4]),
                created_at=r[5],
            )
            for r in rows
        ]

    # ------------------------------------------------------------ 검색

    async def search(
        self,
        query: str,
        scope: Literal["all", "project"],
        project_path: str | None,
        limit: int = 20,
    ) -> list[SearchHit]:
        """FTS5 검색. `project_path`가 주어지면 같은 프로젝트 결과를 먼저 정렬한다.

        `scope="project"`는 `project_path`가 필수이고 그 프로젝트만 돌려준다.
        """
        match = _fts_query(query)
        if not match:
            return []
        if scope == "project" and project_path is None:
            raise ValueError("scope='project' requires project_path")
        phash = project_hash(project_path) if project_path is not None else ""
        where = "fts_events MATCH ?"
        params: list[Any] = [phash, match]
        if scope == "project":
            where += " AND s.project_hash = ?"
            params.append(phash)
        params.append(limit)
        sql = (
            "SELECT e.session_id, s.project_path, e.created_at, e.seq, e.kind, e.payload, "
            "s.project_hash = ? AS same_project "
            "FROM fts_events JOIN events e ON e.id = fts_events.rowid "
            "JOIN sessions s ON s.id = e.session_id "
            f"WHERE {where} "
            "ORDER BY same_project DESC, bm25(fts_events), e.created_at DESC LIMIT ?"
        )
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        hits: list[SearchHit] = []
        for r in rows:
            text = _indexable_text(r[4], json.loads(r[5])) or ""
            hits.append(
                SearchHit(
                    session_id=r[0],
                    project_path=r[1],
                    created_at=r[2],
                    seq=r[3],
                    kind=r[4],
                    text=text,
                    same_project=bool(r[6]),
                )
            )
        return hits

    async def close(self) -> None:
        await self._conn.close()
