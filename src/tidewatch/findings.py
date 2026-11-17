"""Findings and the report: Tidewatch's versioned output contract (ADR 0003)."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "1"
Severity = Literal["critical", "warning", "info"]
SEVERITY_RANK: dict[str, int] = {"critical": 0, "warning": 1, "info": 2}
MAX_EVIDENCE = 5
MAX_LOG_LINES = 3

# Key-like tokens in log lines (provider errors often echo part of a key). Masked in every
# log_excerpt, for every rule. This is a guard rail, not redaction (see ADR 0003/0004).
_MASKS = [
    (re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{6,}"), r"\1-***"),
    (re.compile(r"\b(Bearer)\s+\S+", re.IGNORECASE), r"\1 ***"),
    (re.compile(r"\b(Authorization\s*[=:]\s*Basic)\s+\S+", re.IGNORECASE), r"\1 ***"),
    (  # key=value, key: value, JSON-quoted "key": "value"; prefixed names like client_secret
        re.compile(
            r"\b([A-Za-z0-9_-]{0,30}?(?:api[_-]?key|token|secret|password))"
            r"([\"']?\s*[=:]\s*[\"']?)[^\s\"',}]+",
            re.IGNORECASE,
        ),
        r"\1\2***",
    ),
    # A long run with both letters and digits; no "/", "-" or "." so URLs, hostnames and file
    # paths keep their (short) segments. Checked in a function so time stays linear in length.
    (re.compile(r"[A-Za-z0-9_+=]{24,}"), lambda m: _mask_if_mixed(m)),
]


def _mask_if_mixed(m: re.Match) -> str:
    run = m.group(0)
    # Provider request IDs stay visible: they're what the provider's support asks for first.
    # "_" and "=" are token characters, so `request_id=req_...` is one run: check its value part.
    # "-" isn't, so `chatcmpl-...` starts a new run: check the text just before it.
    value = run.rpartition("=")[2]
    if value.startswith("req_") or m.string[max(0, m.start() - 9) : m.start()] == "chatcmpl-":
        return run
    has_digit = any(ch.isdigit() for ch in run)
    has_letter = any(ch.isalpha() for ch in run)
    return "***" if has_digit and has_letter else run


def mask_secrets(line: str) -> str:
    for pattern, replacement in _MASKS:
        line = pattern.sub(replacement, line)
    return line


class Finding(BaseModel):
    rule_id: str
    severity: Severity
    title: str
    object: str = Field(description='"Kind/name" of the affected object.')
    container: str | None = None
    summary: str
    evidence: list[str] = Field(
        default_factory=list, description=f"Derived facts only, at most {MAX_EVIDENCE}."
    )
    log_excerpt: list[str] = Field(
        default_factory=list,
        description=(
            f"Raw log lines, at most {MAX_LOG_LINES}. Kept apart from evidence so redaction "
            "and the AI-summary step can drop them."
        ),
    )
    next_steps: list[str] = Field(default_factory=list)

    @field_validator("evidence")
    @classmethod
    def _cap_evidence(cls, value: list[str]) -> list[str]:
        return value[:MAX_EVIDENCE]

    @field_validator("log_excerpt")
    @classmethod
    def _cap_logs(cls, value: list[str]) -> list[str]:
        return [mask_secrets(line) for line in value[:MAX_LOG_LINES]]


class Report(BaseModel):
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    context: str | None
    namespace: str
    snapshot_collected_at: datetime
    counts: dict[str, int]
    findings: list[Finding] = Field(default_factory=list)
