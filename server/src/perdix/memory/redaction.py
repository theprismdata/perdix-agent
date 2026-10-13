import re
from typing import Final

_PATTERNS: Final = (
    re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(?:sk-(?:proj-|ant-)?[\w-]{10,}|gh[pousr]_[\w]{20,}|github_pat_[\w]+)\b"),
    re.compile(r"\bBearer\s+[\w.+/=-]+", re.I),
    re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b"),
    re.compile(
        r"(?i)(?:[\w-]*(?:api[_-]?key|token|password|passwd|secret)[\w-]*)"
        r"\s*[:=]\s*[\"']?[^\s\"',;}]+"
    ),
    re.compile(r"https?://[^\s/@]+:[^\s/@]+@[^\s]+"),
)


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
