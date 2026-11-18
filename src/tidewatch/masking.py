"""Minimal redaction: mask key-like tokens in log lines and messages (ADR 0005)."""

import re

# Key-like tokens (provider errors often echo part of a key). Applied to every log line and
# free-text message a snapshot holds, and to every finding's log_excerpt (ADR 0005). Pattern-based
# masking: it catches common key shapes, not every possible secret.
_MASKS = [
    (re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{6,}"), r"\1-***"),
    # `sk_` keys: keep the prefix, mask the rest when it has a digit.
    (re.compile(r"\bsk_[A-Za-z0-9_]{20,}"), lambda m: _mask_sk_underscore(m)),
    (re.compile(r"\b(Bearer)\s+\S+", re.IGNORECASE), r"\1 ***"),
    (re.compile(r"\b(Authorization\s*[=:]\s*Basic)\s+\S+", re.IGNORECASE), r"\1 ***"),
    # key=value, key: value, JSON-quoted "key": "value" (also escaped: \"key\": \"value\"), and
    # prefixed names like client_secret.
    (
        re.compile(
            r"\b([A-Za-z0-9_-]{0,30}?(?:api[_-]?key|token|secret|password))"
            # the value stops at a backslash only before a quote (an escaped JSON end), so a
            # plain value containing a backslash is masked whole
            r"(\\?[\"']?\s*[=:]\s*\\?[\"']?)(?:[^\s\"',}\\]|\\(?![\"']))+",
            re.IGNORECASE,
        ),
        r"\1\2***",
    ),
    # A long run with both letters and digits; no "/", "-" or "." so URLs, hostnames and file
    # paths keep their (short) segments. Checked in a function so time stays linear in length.
    (re.compile(r"[A-Za-z0-9_+=]{24,}"), lambda m: _mask_if_mixed(m)),
]


REQUEST_ID_KEY = re.compile(r"request[_-]id\\?[\"']?\s*[:=]\s*\\?[\"']?$", re.IGNORECASE)
HEX32 = re.compile(r"[0-9a-f]{32}")


def _mask_sk_underscore(m: re.Match) -> str:
    return "sk_***" if any(ch.isdigit() for ch in m.group(0)) else m.group(0)


def _mask_if_mixed(m: re.Match) -> str:
    run = m.group(0)
    # Provider request IDs stay visible: they're what the provider's support asks for first.
    # "_" and "=" are token characters, so `request_id=req_...` is one run: check its value part.
    # "-" isn't, so `chatcmpl-...` starts a new run: check the text just before it.
    # Image digests (`@sha256:<hex>`) aren't secrets; support needs them to tell which image ran.
    # ElevenLabs request_ids are 32 hex characters with no prefix: kept only as the value of a
    # request_id / request-id / x-request-id key (https://elevenlabs.io/docs/eleven-api/resources/errors).
    value = run.rpartition("=")[2]
    before = m.string[max(0, m.start() - 9) : m.start()]
    if value.startswith("req_") or before == "chatcmpl-" or before.endswith("sha256:"):
        return run
    key = m.string[max(0, m.start() - 40) : m.start()] + run[: len(run) - len(value)]
    if HEX32.fullmatch(value) and REQUEST_ID_KEY.search(key):
        return run
    has_digit = any(ch.isdigit() for ch in run)
    has_letter = any(ch.isalpha() for ch in run)
    return "***" if has_digit and has_letter else run


def mask_secrets(line: str) -> str:
    for pattern, replacement in _MASKS:
        line = pattern.sub(replacement, line)
    return line
