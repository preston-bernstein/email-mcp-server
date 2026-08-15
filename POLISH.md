# Codebase Polish — 2026-08-15

Second pass, ~4 days after the 2026-08-11 pass below. Repo state going in: clean working tree except an untracked `.claude/` (pre-existing local skill docs, out of scope for a code polish pass — left untouched, not committed), no divergence from `origin/main`.

## Standards Brief sources
- `deadcode` (the preferred tool per this run's brief) is incompatible with the local Python 3.14 interpreter (`AttributeError: module 'ast' has no attribute 'Str'` — the library still targets pre-3.12 `ast` APIs). Fell back to `vulture --min-confidence 60`, run in an isolated venv.
- Google eng-practices code review culture (improve code health, not perfectionism) — same baseline as the prior pass.
- Given the repo's size (695 LOC, one module + one test file), findings this pass were deliberately few: the 2026-08-11 pass already did the heavy lifting (session/logout leaks, Bcc disclosure, date-math bug, socket timeouts, thin-async wrappers, dead-code removal). This pass is a maintenance-scale check, not a rewrite.

## Deterministic signal
`vulture --min-confidence 60` on `proton_email_server.py` + `tests/test_proton_email_server.py`: 3 raw findings, all 3 the same mock-protocol false positives already documented in the 2026-08-11 pass (`side_effect`, `__enter__`, `__exit__` in the test file's mocks — consumed by `unittest.mock`/`with`, not dead). Zero new dead code in the application module itself — confirms the prior pass's cleanup held.

## Applied
- **[SHOULD, correctness]** `extract_email_body` (`proton_email_server.py`): a multipart/alternative message (the common case — most mail clients send matching `text/plain` + `text/html` parts of the *same* content) was concatenating both parts into one `body` string, so a tool response could return the plain-text version immediately followed by a dump of raw HTML markup for the same message. Now returns the `text/plain` part when present, and only falls back to `text/html` when no plain part exists. Verified by hand against three cases (plain+html, html-only, non-multipart) — all correct; no existing test exercised this path so nothing broke.
- **[SHOULD, observability]** `imap_session`'s cleanup `finally` block silently swallowed any exception from `mail.logout()` (`except Exception: pass`) — a real logout failure (e.g. Bridge closing the connection oddly) left zero log trace. Now logs it at `debug` level, consistent with the existing pattern for other non-fatal, log-and-continue paths in this file (e.g. the TEXT→SUBJECT search fallback).
- **[NIT]** Module-startup exception handler used `logger.error(f"Server error: {e}", exc_info=True)`; changed to `logger.exception("Server error")` for consistency with the rest of the file's `_log_failure` helper, which already uses `logger.exception`.
- **[NIT]** `requirements.txt` was missing a trailing newline. Added.

## Escalated
None. Every finding this pass was an internal-implementation fix (no `@mcp.tool()` signature or behavior-contract change) and none touched code that looked deliberately-there-for-a-reason.

## Regression safety net
- `pytest tests/` (isolated venv, deps from `requirements.txt` + `pytest`/`pytest-asyncio`) — 12/12 passed, same as baseline before this pass's edits.
- Clean-import sanity check (`python -c "import proton_email_server"` with dummy credentials in env) — passed.
- Manual verification of the `extract_email_body` fix against multipart/alternative (plain+html), html-only, and non-multipart inputs — all three returned the expected body.

## Not applied / left as-is
- `logging.basicConfig()` staying hand-rolled rather than adopting the portfolio's shared `fleet-logging` — judged fine again this pass; this is a stdio MCP server that logs straight to stderr, which is the correct convention for this transport, not a gap a shared HTTP/file-sink logging lib would close.
- No new duplication/module-boundary tooling introduced — single-module repo, still speculative generality per the YAGNI guardrail.
- `.claude/` (untracked, pre-existing local skill docs) left alone — not code, out of scope for this pass, not committed.

---

# Codebase Polish — 2026-08-11

One pass over the whole repo (single file `proton_email_server.py` + its test file). Grounded in a live best-practices sweep (see Standards Brief below), reviewed by three parallel lenses, applied directly, then checked for regressions.

## Standards Brief sources
- Live advisor sweep run this session (topic: DRY/dead-code/duplication, architecture/module-boundary, observability — as of 2026-08-11), refreshing the existing vault doc at `Development/Research/codebase-quality-polish-skill.md`. Verdict for a repo this size: Ruff + Vulture for dead-code, skip module-boundary tooling (Import Linter/Tach) until the repo passes ~3-4 files, Loguru-style structured logging over full structlog/OTel.
- Google eng-practices code review culture (improve code health, not perfectionism).
- Deterministic tool: `vulture --min-confidence 60` (installed). `slopo` for Python duplication detection was not installed — duplication triage relied on the judgment lens agent instead of a dedicated tool.

## Pre-pass note
The repo had an uncommitted WIP change (`from_email` alias param on `send_email`) when this pass started. Per your choice, it was committed separately first (`342ec4d`) so this pass's diff wouldn't mix with it.

## Applied — Elegance/Architecture/DRY
- Extracted `imap_session()` (contextlib contextmanager): connect + login + optional folder-select-with-status-check + guaranteed `logout()` in `finally`. Replaces four near-identical, independently-drifted copies of this boilerplate in `read_recent_emails`, `search_emails`, `list_folders`, `get_email_stats`.
- Extracted `_missing_credentials()`, `_parse_limit()`, `_most_recent()`, `_split_addrs()`, `_format_message()` — each was duplicated 2-5 times across the file with minor drift (e.g. only `search_emails` previously set a socket timeout or checked select status).
- Moved `import re` and `import ssl` (previously function-local, underscore-aliased `_ssl`) to the top-level import block; dropped the misleading underscore aliasing.
- Removed a stale shouted comment (`# NO PROMPT PARAMETER!`) that no longer corresponded to anything nearby.
- **[MUST, correctness]** `send_email` (`proton_email_server.py`): fixed a Bcc-disclosure bug — the code set a `Bcc` MIME header, which SMTP servers include in the sent message, disclosing blind-copy recipients to everyone. Bcc addresses now only go into the `to_addrs` recipient list, never the header.
- **[MUST, correctness]** `get_email_stats`: "Recent emails (7 days)" was actually a today-only count (`SINCE datetime.now()`). Fixed to `SINCE (datetime.now() - timedelta(7))`.
- **[MUST, resource-leak]** All four IMAP tools now guarantee `mail.logout()` on every exit path (via `imap_session`'s `finally`), not just the happy path.
- **[SHOULD]** Narrowed `except ValueError` blocks so they only wrap the actual `int()` parse, not the whole tool body (previously an unrelated `ValueError` anywhere in the function would be mis-reported as "Invalid count value").
- **[SHOULD]** Restructured all 5 `@mcp.tool()` functions to be thin `async` wrappers (`return await asyncio.to_thread(_<name>_sync, ...)`) around plain synchronous implementations — the tools were declared `async` but did only blocking socket I/O, stalling the event loop under the `streamable-http` transport the Dockerfile ships.

## Applied — Dead-code & duplication triage
- Removed unused `timezone` import (never referenced; the 7-day fix above uses `timedelta` instead).
- Removed unused `from mcp.server.fastmcp import FastMCP as _FastMCP` re-import inside the streamable-http startup branch — genuinely dead, not framework magic (the module already uses the top-level `mcp` instance).
- Removed unused `call` import from `tests/test_proton_email_server.py`.
- Confirmed as false positives, left untouched: `ssl.SSLContext.check_hostname`/`verify_mode` (write-only config attributes, not dead) and three mock-protocol attributes in the test file (`side_effect`, `__enter__`, `__exit__` — consumed internally by `unittest.mock`/`with`, not dead).
- Deduplicated the "take last N ids, most-recent-first" and "extract subject/sender/date and format" logic that was copy-pasted between `read_recent_emails` and `search_emails`.

**Vulture raw count vs. survived triage:** 8 raw findings → 3 confirmed real and fixed, 5 confirmed false positives (framework/mock internals) and left alone.

## Applied — Observability & monitoring
- **[MUST]** All IMAP/SMTP connections now get a socket timeout (previously only `search_emails` set one; `read_recent_emails`, `list_folders`, `get_email_stats`, and the SMTP connection in `send_email` could hang indefinitely against an unresponsive Proton Bridge with zero log signal).
- **[MUST]** Replaced `logger.error(f"...: {e}")` with `logger.exception(...)` everywhere, so tracebacks are actually captured — previously a decode bug and a Bridge protocol error were indistinguishable in the logs.
- **[MUST]** `read_recent_emails` and `get_email_stats` now check `mail.select()`'s return status (via `imap_session`) — previously a bad/inaccessible folder silently fell through and was misreported as "no emails found" / zero stats, with no log line indicating selection actually failed.
- **[SHOULD]** Added a light failure-class tag (`protocol error` / `network error` / `unexpected error`, based on exception type) to every logged failure, without introducing a full custom-exception hierarchy — judged disproportionate to add 3 separate `except` clauses per tool (5 tools × 3 clauses) for a single-file, single-operator service at this scale.
- **[SHOULD]** `search_emails`' TEXT→SUBJECT fallback now logs the exception that triggered the fallback (`logger.debug`) instead of swallowing it silently.
- **[SHOULD]** `list_folders` now logs a warning (with the raw line) when a folder-list entry doesn't match the expected quoted format, instead of silently dropping it from the result.
- **[SHOULD]** `read_recent_emails` and `search_emails` now catch per-message fetch/parse failures and continue (logging + a placeholder line for that one message) instead of one malformed email aborting the whole batch and discarding already-built results.
- Not applied: adding a new structured-logging dependency (Loguru) — the existing stdlib `logging` setup, once given real tracebacks and failure-class tags, already closes the gap the advisor sweep flagged; a new dependency wasn't a clean enough win to justify for this repo's size.

## Escalated to you (resolved this session)
1. **TLS posture** (SMTP cert verification disabled + IMAP running unencrypted) — you chose to keep the existing behavior and have it documented + guarded rather than changed. Applied: a comment explaining the self-signed-Bridge-cert rationale, and an `assert PROTON_BRIDGE_HOST in ("127.0.0.1", "localhost", "::1")` before the cert-verification bypass, so the bypass can't silently apply if `PROTON_BRIDGE_HOST` is ever pointed at a non-loopback host.
2. **`count`/`max_results` param types** (str vs int) — you chose to leave them as `str` with the existing hand-parsed validation, since retyping to `int` would change the published MCP tool schema. No change made.

## Regression safety net
- `pytest tests/` — 12/12 passed, both before committing and independently re-verified by a separate correctness-focused review pass.
- A dedicated correctness/regression review (bug-focus only, not elegance) was run against this pass's full diff. It traced every call site's argument wiring, the `imap_session` exception/logout semantics, the Bcc fix, the date-math fix, and the TLS-guard's interaction with the default config. Result: no regressions found; confirmed the three fixes above are genuine, behavior-preserving elsewhere.
- No Playwright-drivable surface exists for this repo (no UI, no reachable HTTP endpoint without live Proton Bridge credentials) — the pytest suite (mocked IMAP/SMTP) is the correct and only available verification surface, and it was exercised as required. EXEMPT is explicit, not a silent skip.

## Not applied / left as-is
- No module-boundary tool introduced (Import Linter/Tach) — single-file repo, would be speculative generality per the YAGNI guardrail.
- No `slopo`-equivalent duplication tool run — none installed for Python; the architecture lens covered logic-level duplication directly instead.
