"""Findings and the report: Tidewatch's versioned output contract (ADR 0003)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from tidewatch.masking import mask_secrets

SCHEMA_VERSION = "1"
Severity = Literal["critical", "warning", "info"]
SEVERITY_RANK: dict[str, int] = {"critical": 0, "warning": 1, "info": 2}
MAX_EVIDENCE = 5
MAX_LOG_LINES = 3


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
