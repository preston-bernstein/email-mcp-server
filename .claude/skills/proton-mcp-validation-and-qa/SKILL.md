---
name: proton-mcp-validation-and-qa
description: >-
  Evidence standards and test discipline for proton-email-mcp. Load this BEFORE
  claiming any change works, before saying "verified" or "tested" or "done",
  when adding or modifying tests in tests/test_proton_email_server.py, when
  deciding what evidence a change needs before commit, when writing a
  regression test for a bug fix, when a test fails or hangs unexpectedly, or
  when asked "how do I test this" / "is this safe to test" / "did the tests
  pass". Covers the evidence ladder (mocked pytest -> boot smoke ->
  connectivity -> read-only live -> gated send test), the full inventory of
  all 12 tests and known coverage gaps, and the exact recipe for adding a
  test (env-before-import trap, mock helpers, asyncio marker, stdlib patch
  paths).
---

# Validation and QA — proton-email-mcp

What counts as evidence in this repo, and how to add tests without stepping on the traps.

Scope: the mocked pytest suite in `tests/test_proton_email_server.py`, the evidence
ladder for accepting a change to `proton_email_server.py`, and the recipe for new tests.
This skill does NOT tell you how to run the server, probe the Bridge, or classify a
change — see "When NOT to use this skill".

Jargon, defined once:

- **Proton Bridge** — local daemon that exposes a Proton Mail account as plain
  IMAP/SMTP on localhost-style ports. The server under test speaks to it; the test
  suite never does (everything is mocked).
- **MCP** — Model Context Protocol. The server exposes 5 email tools over MCP.
- **Mocked suite** — `tests/test_proton_email_server.py`: patches `imaplib.IMAP4`
  and `smtplib.SMTP`, needs no Bridge, no network, no credentials. Safe to run anywhere.

## The one rule

**"Eyeballing output looked fine" is never evidence.** Every claim of "works" /
"fixed" / "verified" must name the rung of the evidence ladder it was verified at,
and that rung must be appropriate to the change. If you cannot run the required
rung, say so explicitly and label the claim UNVERIFIED.

## Evidence ladder

A change is "verified" only at the right rung. Higher rungs assume the lower ones
already passed. Rung 1 is the mandatory floor for ANY code change.

| Rung | What | Proves | Command / procedure | Gate |
|---|---|---|---|---|
| 1 | Mocked pytest suite | Logic correctness of tool functions | `cd /Users/prestonbernstein/dev/proton-email-mcp && ./venv/bin/python -m pytest tests/ -q` | Mandatory for every code change. All tests pass (12 as of 2026-07-02), PLUS a new test for any new behavior. |
| 2 | Local server boot smoke | Module imports, MCP server starts, transport wiring intact | Start `./venv/bin/python proton_email_server.py` with stdio transport; confirm startup log lines appear on **stderr** and NOTHING is printed to **stdout** (stdout is the MCP stdio channel — any pollution breaks the protocol). Kill it after startup. | Required when touching imports, module top-level, the `FastMCP` constructor, or the startup block (lines ~325-346). |
| 3 | Connectivity probes | Bridge IMAP/SMTP ports reachable from this host | Defer entirely to sibling skill **proton-mcp-diagnostics-and-tooling** — do not improvise probes here. | Required before any live testing (rungs 4-5). |
| 4 | Read-only live integration | End-to-end against real Bridge, no mutations | Call `list_folders`, `get_email_stats`, `read_recent_emails` via a real MCP session (e.g. the `mcp__proton-mail__*` tools in Claude Code). | Acceptable without human sign-off — these are read-only. Required for changes to IMAP handling, config/env parsing, or deploy artifacts. |
| 5 | SEND path live test | `send_email` works end-to-end | Send ONE email, ONLY to the account's own address, ONLY after explicit human confirmation in this session. | ASSUMPTION (coordinator-sanctioned, no repo doc states it): never send without explicit human approval; never to a third-party address during dev/test. |

Matching rung to change:

- Docstring/comment-only change: rung 1 (suite still green) is enough.
- New/changed logic inside a tool or helper: rung 1 with a new test; rung 4 if IMAP/SMTP interaction shape changed.
- Startup/transport/dependency change: rungs 1 + 2; rung 4 before redeploy.
- Anything touching `send_email`'s send path: rungs 1 + 2 + 4, and rung 5 only with the human gate above.

The mocks tell you nothing about the real Bridge's dialect (e.g. whether it accepts
`TEXT` search) — that is exactly why rung 4 exists and why rung 1 alone never
justifies "works in production".

## Golden inventory — the 12 tests (as of 2026-07-02)

All in `/Users/prestonbernstein/dev/proton-email-mcp/tests/test_proton_email_server.py`.
Suite result today: `12 passed in 0.18s`.

| # | Test | Certifies |
|---|---|---|
| 1 | `test_search_strips_special_chars` | Query sanitization: `~` and other illegal chars are stripped BEFORE the IMAP `search` call (regression guard for the pre-git IMAP hang incident). |
| 2 | `test_search_empty_after_sanitization` | A query that sanitizes to nothing returns a `❌` error and `imaplib.IMAP4` is **never called** — the no-network guarantee (`MockIMAP.assert_not_called()`). |
| 3 | `test_search_falls_back_to_subject_when_text_fails` | If the `TEXT` search raises, a second `SUBJECT` search is issued (exactly 2 calls, second contains `SUBJECT`). |
| 4 | `test_search_no_results` | Empty search result yields the friendly "No emails found" message, not an error. |
| 5 | `test_search_bad_folder` | Non-OK `select` status returns a `❌` error mentioning the folder — no fetch attempted. |
| 6 | `test_search_socket_timeout_set` | `mail.sock.settimeout(15)` is called exactly once (regression guard for the indefinite-hang incident). |
| 7 | `test_read_recent_emails_returns_emails` | Basic read path: subject and sender from a fetched RFC822 message appear in output. |
| 8 | `test_read_recent_emails_limit_cap` | `count="999"` is capped (safety limit 50) and fetch still proceeds without error. |
| 9 | `test_send_email_success` | Happy-path send returns a success message (`✅`/"sent") using a mocked SMTP context manager. |
| 10 | `test_send_email_missing_fields` | Missing `to_email` or `subject` each return `❌` without attempting SMTP. |
| 11 | `test_list_folders` | Folder listing parses the mocked IMAP LIST response and includes INBOX. |
| 12 | `test_get_email_stats` | Stats tool runs against mocked search results and reports counts. |

### What is NOT covered (verified gaps — do not claim these are tested)

- The startup/transport block (`__main__`, stdio vs streamable-http selection, uvicorn path).
- `extract_email_body` multipart edge cases (attachments, HTML-only, nested parts, odd charsets).
- `decode_email_header` edge cases (encoded-word headers, broken encodings).
- cc/bcc recipient-list assembly in `send_email` (test #9 sends with no cc/bcc).
- The **open stats bug**: `get_email_stats` labels a count "Recent emails (7 days)" but
  uses `SINCE <today>` (`datetime.now().strftime("%d-%b-%Y")`, line 307) — it counts
  TODAY only. Test #12 does not catch this. Any fix MUST start with a failing
  regression test (see Acceptance discipline).

## How to add a test — the house pattern

Read `tests/test_proton_email_server.py` first; new tests must match its shape.

### Trap #1 (the one that bites everyone): env vars BEFORE import

`proton_email_server.py` reads `PROTON_USERNAME` etc. into module-level constants
**at import time** (lines 29-33). The test file therefore injects env vars via
`os.environ.setdefault` BEFORE `import proton_email_server`:

```python
# This ordering is load-bearing. Do not "clean it up".
os.environ.setdefault("PROTON_USERNAME", "test@protonmail.com")   # fixture value, not real
os.environ.setdefault("PROTON_PASSWORD", "test-bridge-password")  # fixture value, not real
os.environ.setdefault("PROTON_BRIDGE_HOST", "127.0.0.1")
os.environ.setdefault("PROTON_BRIDGE_IMAP_PORT", "1143")
os.environ.setdefault("PROTON_BRIDGE_SMTP_PORT", "1025")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import proton_email_server as srv
```

If an import formatter/linter moves the import above the env block, every
credential-dependent test silently exercises the "credentials not configured" branch
instead. If a test unexpectedly asserts on `❌ Error: Proton credentials not
configured`, check import order first.

### The rest of the recipe

1. **Reuse the helpers.** `mock_imap(mail_ids=..., raw_email=..., select_status=...)`
   returns a pre-wired `MagicMock` IMAP instance (select/search/fetch/list responses,
   `.sock` mock). `make_raw_email(subject=..., sender=..., body=...)` builds RFC822
   bytes. Extend the helpers rather than hand-rolling new mocks.
2. **Patch at the stdlib path.** The module does `import imaplib` / `import smtplib`,
   so patch `"imaplib.IMAP4"` and `"smtplib.SMTP"` — NOT
   `"proton_email_server.imaplib..."`-style paths (those also work for module attribute
   access, but the house pattern is the stdlib path; stay consistent).
3. **Mark async tests.** Every tool is `async`; tests need `@pytest.mark.asyncio`.
   There is NO pytest config file in this repo (no pytest.ini/pyproject.toml, verified
   2026-07-02), so pytest-asyncio (1.4.0) runs in strict marker-based mode — an
   unmarked async test is skipped/errored, not run.
4. **Call tools directly.** `@mcp.tool()` in mcp 1.28.0 (installed version, verified)
   returns the original function unchanged, so `await srv.search_emails(...)` just
   works. No MCP client harness needed at rung 1.
5. **Assert on behavior, not emoji trivia.** House convention: error paths return
   strings starting with `❌`; assert `"❌" in result` plus something specific
   (folder name, "No emails found", call counts, `assert_not_called()`).
6. **SMTP mocks need context-manager wiring** (the server uses
   `with smtplib.SMTP(...) as server:`) — copy the `__enter__`/`__exit__` setup from
   `test_send_email_success`.

Skeleton:

```python
@pytest.mark.asyncio
async def test_my_new_behavior():
    m = mock_imap(mail_ids=[b"1"])
    with patch("imaplib.IMAP4", return_value=m):
        result = await srv.read_recent_emails(count="1", folder="INBOX")
    assert "expected thing" in result
```

## Acceptance discipline

- **Full suite green before any commit.** No exceptions, no "unrelated failure" waivers.
- **Bug fixes ship regression-first.** Write the test that reproduces the bug, watch
  it FAIL against current code, then fix, then watch it pass. The open stats 7-day
  bug is the standing example: any fix to `get_email_stats` must include a test that
  fails on the `SINCE <today>` behavior first.
- **Test count is a monotonic ratchet.** 12 as of 2026-07-02; it only goes up. Never
  delete or skip a test to get green. Deleting a test is a behavior-change decision —
  route it through the sibling skill proton-mcp-change-control.
- **New behavior = new test.** A code change without a corresponding test does not
  clear rung 1, even if the existing 12 still pass.
- Report evidence explicitly, e.g.: "rung 1: 13 passed in 0.2s (new
  test_stats_seven_day_window fails pre-fix, passes post-fix); rung 4 not run."

## Run commands

All from the repo root; always use the venv interpreter (the venv is Python 3.14 with
pytest 9.1.1 / pytest-asyncio 1.4.0 installed — system python will lack deps).

```bash
cd /Users/prestonbernstein/dev/proton-email-mcp

# Quick (the default check)
./venv/bin/python -m pytest tests/ -q

# Verbose (one line per test, for the golden-inventory view)
./venv/bin/python -m pytest tests/ -v

# Single test / substring match
./venv/bin/python -m pytest tests/ -k test_search_socket_timeout_set -v
./venv/bin/python -m pytest tests/ -k "search" -q
```

Expected (as of 2026-07-02): `12 passed` in roughly 0.2-0.3s. The suite is fully
mocked — if it takes seconds or appears to hang, something is making a real network
call; that is a test bug. Stop and find the unpatched connection.

## When NOT to use this skill

| You actually want to... | Use instead |
|---|---|
| Understand design invariants or known-weak points before changing code | proton-mcp-architecture-contract |
| Classify a change, decide gates/review, remove a dependency | proton-mcp-change-control |
| Debug a live failure (hangs, auth errors, empty results) | proton-mcp-debugging-playbook |
| Recreate the venv or Docker image, fix env-setup traps | proton-mcp-build-and-env |
| Run/deploy the server (stdio or streamable-http), config table | proton-mcp-run-and-operate |
| Probe Bridge connectivity, run read-only smoke scripts, check source drift | proton-mcp-diagnostics-and-tooling |
| Add or modify an MCP tool (design recipe, not test recipe) | proton-mcp-extending-tools |
| Fix source drift or harden the unauthenticated :3004 transport | proton-mcp-drift-and-hardening-campaign |
| Learn Proton Bridge / IMAP / SMTP / MIME / MCP transport concepts | proton-bridge-email-reference |

Also do not use this skill as permission to run live tests: rung 3+ procedures live
in the siblings above, and rung 5 (send) always requires explicit human confirmation.

## Provenance and maintenance

- Authored 2026-07-02 from three ground-truth sources read in full that day:
  `proton_email_server.py` (346 lines), `tests/test_proton_email_server.py`
  (12 tests), and the coordinator's verified facts pack (§6 tests/validation,
  §7 incidents, §9 discipline, §10 sibling list).
- Suite executed during authoring: `./venv/bin/python -m pytest tests/ -q` →
  `12 passed in 0.18s` (2026-07-02).
- Items labeled ASSUMPTION (the send-test human-approval gate) are
  coordinator-sanctioned conventions with no repo document behind them; if a repo
  policy doc appears, it supersedes this skill's wording.
- Volatile facts to re-verify when maintaining this skill: the test count (ratchet
  floor 12 as of 2026-07-02), mcp package version (1.28.0 — the "tools are directly
  callable" claim depends on `@mcp.tool()` returning the original function),
  absence of a pytest config file (strict asyncio marker mode depends on it), and
  the open stats 7-day bug (delete that gap entry once a regression test + fix land).
- Update this file whenever a test is added/renamed, a coverage gap is closed, or
  the evidence ladder changes (e.g. CI appears — there is none as of 2026-07-02).
