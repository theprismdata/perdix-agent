"""권한 정책: allow | ask | deny. 모드 의미는 docs/protocol.md §1.3."""

from __future__ import annotations

import fnmatch
import posixpath
import re
import shlex
from collections.abc import Mapping
from typing import Any, Literal

Decision = Literal["allow", "ask", "deny"]
PermissionMode = Literal["interactive", "deny", "accept-edits", "allowlist", "yolo"]
Rule = Decision | dict[str, Decision]

DEFAULT_RULES: dict[str, Rule] = {
    "team": "allow",
    "switch_mode": "allow",
    "read": "allow",
    "glob": "allow",
    "grep": "allow",
    "ls": "allow",
    "memory_read": "allow",
    "session_search": "allow",
    "websearch": "allow",
    "webfetch": "allow",
    "memory_append": "ask",
    "edit": "ask",
    "write": "ask",
    "bash": "ask",
}
# deny 모드에서도 allow로 남는 내장 읽기 전용 도구
READ_ONLY_BUILTINS: frozenset[str] = frozenset(
    {"read", "glob", "grep", "ls", "memory_read", "session_search", "switch_mode"}
)
EDIT_TOOLS: frozenset[str] = frozenset({"edit", "write"})
# 이런 문자가 있으면 연결·치환·리다이렉션이 있는 복합 명령으로 보고 allow하지 않는다(ask)
SHELL_CONTROL_CHARS: tuple[str, ...] = (";", "&", "|", "`", "$(", ">", "<", "\n")
_SEVERITY: dict[Decision, int] = {"allow": 0, "ask": 1, "deny": 2}
# shlex 구두점. 이 중 `_SEGMENT_BREAKS`를 포함한 토큰은 단순 명령 경계다.
_PUNCTUATION = ";&|<>()\n"
_SEGMENT_BREAKS = frozenset(";&|()\n")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# 실제 프로그램을 감싸는 래퍼와, 값을 하나 더 받는 옵션
_WRAPPERS: dict[str, frozenset[str]] = {
    "command": frozenset(),
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
    "exec": frozenset({"-a"}),
    "sudo": frozenset({"-u", "-g", "-U", "-C", "-D", "-p", "-r", "-t", "-T", "-h"}),
    "nohup": frozenset(),
    "time": frozenset({"-f", "-o"}),
}


def _subject(tool_name: str, input: Mapping[str, Any]) -> str:
    """패턴 규칙이 매칭할 문자열. bash는 명령, 나머지는 path."""
    key = "command" if tool_name == "bash" else "path"
    value = input.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _match_patterns(patterns: Mapping[str, Decision], subject: str) -> Decision:
    """가장 구체적인(와일드카드 제외 글자가 많은) 매칭 패턴이 이긴다. 동률이면 더 엄격한 쪽."""
    best: tuple[int, int, Decision] | None = None
    for pattern, decision in patterns.items():
        if not fnmatch.fnmatchcase(subject, pattern):
            continue
        specificity = len(pattern.replace("*", "").replace("?", ""))
        candidate = (specificity, _SEVERITY[decision], decision)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best is not None else "ask"


def split_simple_commands(command: str) -> list[list[str]] | None:
    """명령을 `;` `&&` `||` `|` `&` 줄바꿈 괄호 경계로 단순 명령 단어 목록들로 나눈다.

    백틱은 경계로 바꿔 안쪽 명령도 검사 대상이 되게 한다. 리다이렉션 연산자 토큰은 버린다.
    따옴표가 맞지 않는 등 shlex가 실패하면 None.
    """
    lexer = shlex.shlex(command.replace("`", " ; "), posix=True, punctuation_chars=_PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    lexer.whitespace_split = False
    segments: list[list[str]] = []
    current: list[str] = []
    try:
        for token in lexer:
            if token and all(c in _PUNCTUATION for c in token):
                if any(c in _SEGMENT_BREAKS for c in token):
                    if current:
                        segments.append(current)
                    current = []
                continue
            current.append(token)
    except ValueError:
        return None
    if current:
        segments.append(current)
    return segments


def normalize_simple_command(words: list[str]) -> tuple[str, bool]:
    """선행 `VAR=value`와 래퍼(`env`, `sudo` 등)를 걷어내고 프로그램을 basename으로 바꾼다.

    돌려주는 bool은 원래 단어와 달라졌는지 여부다.
    """
    rest = list(words)
    while rest:
        if _ASSIGNMENT.match(rest[0]):
            rest.pop(0)
            continue
        takes_value = _WRAPPERS.get(rest[0])
        if takes_value is None:
            break
        rest.pop(0)
        while rest and (rest[0].startswith("-") or _ASSIGNMENT.match(rest[0])):
            option = rest.pop(0)
            if option in takes_value and rest:
                rest.pop(0)
    if not rest:
        return " ".join(words), False
    program = posixpath.basename(rest[0]) or rest[0]
    normalized = [program, *rest[1:]]
    return " ".join(normalized), normalized != words


def _match_bash(patterns: Mapping[str, Decision], command: str) -> Decision:
    """bash 패턴 판정. 어느 단순 명령이든 deny에 걸리면 deny, 복합 명령은 allow 대신 ask."""
    raw = _match_patterns(patterns, command)
    if raw == "deny":
        return "deny"
    segments = split_simple_commands(command)
    normalized = [normalize_simple_command(words) for words in segments or []]
    if any(_match_patterns(patterns, text) == "deny" for text, _ in normalized):
        return "deny"
    compound = (
        segments is None
        or len(segments) > 1
        or any(c in command for c in SHELL_CONTROL_CHARS)
    )
    if compound:
        # "git status*"나 "*"가 "git status; rm -rf ~"를 허용하면 안 된다
        return "ask"
    if normalized and normalized[0][1]:
        # 접두 할당·래퍼·경로가 붙은 명령은 원문 그대로 allow된 것이 아니므로 묻는다
        decision = max(raw, _match_patterns(patterns, normalized[0][0]), key=_SEVERITY.__getitem__)
        return "ask" if decision == "allow" else decision
    return raw


class PermissionPolicy:
    def __init__(
        self,
        rules: Mapping[str, Rule] | None = None,
        mode: PermissionMode = "interactive",
    ) -> None:
        merged: dict[str, Rule] = dict(DEFAULT_RULES)
        merged.update(rules or {})
        self.rules = merged
        self.mode: PermissionMode = mode
        # remember()로 승격된 세션 범위 규칙. 도구 단위 결정과 bash 정확 일치 명령.
        self.session_rules: dict[str, Decision] = {}
        self.session_commands: dict[str, dict[str, Decision]] = {}

    def _resolve(self, tool_name: str, input: Mapping[str, Any]) -> Decision:
        subject = _subject(tool_name, input)
        remembered = self.session_commands.get(tool_name, {}).get(subject)
        if remembered is not None:
            return remembered
        if tool_name in self.session_rules:
            return self.session_rules[tool_name]
        rule = self.rules.get(tool_name, "ask")
        if isinstance(rule, str):
            return rule
        if tool_name == "bash":
            return _match_bash(rule, subject)
        return _match_patterns(rule, subject)

    def evaluate(self, tool_name: str, input: Mapping[str, Any]) -> Decision:
        if self.mode == "yolo":
            return "allow"
        decision = self._resolve(tool_name, input)
        if self.mode == "interactive":
            return decision
        if self.mode == "deny":
            return "allow" if (
                tool_name in READ_ONLY_BUILTINS or tool_name == "team"
            ) and decision == "allow" else "deny"
        if self.mode == "accept-edits":
            if tool_name in EDIT_TOOLS and decision != "deny":
                return "allow"
            return "deny" if decision == "ask" else decision
        # allowlist: 설정의 allow만 통과, 나머지 deny
        return "allow" if decision == "allow" else "deny"

    def remember(
        self, tool_name: str, input: Mapping[str, Any], decision: Literal["allow", "deny"]
    ) -> None:
        """allow_always / reject_always. bash는 해당 명령 정확 일치로만 승격한다."""
        if tool_name == "bash":
            subject = _subject(tool_name, input)
            self.session_commands.setdefault(tool_name, {})[subject] = decision
        else:
            self.session_rules[tool_name] = decision
