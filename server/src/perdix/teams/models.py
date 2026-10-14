from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NAME_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$"


class TeamError(Exception):
    pass


class TeamCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["status", "send", "broadcast", "assign", "complete", "block", "remove", "stop"]
    recipient: str | None = Field(default=None, pattern=NAME_PATTERN)
    body: str = Field(default="", max_length=16_000)
    task_id: str | None = Field(default=None, pattern=NAME_PATTERN)
    paths: list[str] = Field(default_factory=list, max_length=100)


class TeamMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: int
    sender: str
    body: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, project TEXT NOT NULL, stopped INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS members (
 team TEXT NOT NULL, name TEXT NOT NULL, session TEXT NOT NULL,
 role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(team, name)
);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT NOT NULL, sender TEXT NOT NULL,
 recipient TEXT NOT NULL, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
 message INTEGER NOT NULL, session TEXT NOT NULL, PRIMARY KEY(message, session)
);
CREATE TABLE IF NOT EXISTS tasks (
 team TEXT NOT NULL, id TEXT NOT NULL, owner TEXT NOT NULL, body TEXT NOT NULL,
 paths TEXT NOT NULL, state TEXT NOT NULL, result TEXT NOT NULL DEFAULT '', PRIMARY KEY(team, id)
);
CREATE TABLE IF NOT EXISTS team_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT NOT NULL, actor TEXT NOT NULL,
 kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL
);
"""
