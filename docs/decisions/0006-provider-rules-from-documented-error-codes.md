# 0006. Provider-specific rules come from documented error codes
**Status:** Accepted
**Date:** 2026-11-17

## Context
The generic LLM rules (ADR 0004) match wording and status codes: 401/403, 429, timeouts. A specific provider's error body says more than that. For example, ElevenLabs separates a missing key from a bad key, lack of credits from a rate limit, and an unknown voice from a malformed one, and each needs a different fix. Guessed error strings would give confident findings that never fire, or fire on the wrong thing.

## Decision
A provider gets its own rules only for errors its current docs document. Each rule cites its source URL in its docstring. The first provider is ElevenLabs (read 2026-11-17):
- [Errors](https://elevenlabs.io/docs/eleven-api/resources/errors): 401 `invalid_api_key` and `missing_api_key`; 402 `insufficient_credits`; 429 `rate_limit_exceeded`, `concurrent_limit_exceeded` and `system_busy`; 404 `voice_not_found`; 400 `invalid_voice_id`. Also the body shape `{"detail": {"type", "code", "message", "status", "request_id"}}`.
- [API Error Code 400 or 401](https://elevenlabs.io/docs/help-center/technical/api-error-code-400-or-401): `quota_exceeded`, plus `invalid_api_key` and `voice_not_found` in the legacy `status` field.
- [Changelog 2025-05-05](https://elevenlabs.io/docs/changelog/2025/5/5): the 401 message "This request exceeds your quota limit".
- [Authentication](https://elevenlabs.io/docs/api-reference/authentication): the key travels in the `xi-api-key` header.

A code matches only as the value of a `code` or `status` field (`"code": "x"`, `'status': 'x'`, `code=x`, and the escaped `\"code\": \"x\"` of a body logged inside a JSON log line). A line a provider rule matches is left out of the generic LLM rules, so one log line gives one finding, the more specific one. Masking keeps `sk_` key prefixes and masks the rest. ElevenLabs request IDs (32 hex characters, no prefix) stay visible only as the value of a `request_id` key.

## Options considered
- Extend the generic LLM rules with these strings: the next steps would stay generic ("check the key") when the docs give a precise cause.
- Match codes anywhere in a line: config lines such as `retry_on=rate_limit_exceeded` would become findings.
- Keep both findings for one line: a 401 quota message would also say "check the key", which contradicts the quota finding.
- Include codes from older pages or forums (for example `too_many_concurrent_requests`): not in the current docs, so left out.

## Consequences
- Good: each finding names the provider's documented cause and next step, and its source can be checked.
- Good: request IDs stay usable for the provider's support.
- Trade-off: rule IDs name the vendor, so they're part of the report contract. A vendor is named only after the owner approves it (CLAUDE.md).
- Trade-off: the docs change. Rules need a re-read when the provider changes its error format, and only made-up lines in the documented shape test them, with no lab fake provider yet.
