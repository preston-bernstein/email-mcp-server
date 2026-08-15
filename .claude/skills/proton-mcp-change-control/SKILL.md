---
name: proton-mcp-change-control
description: >-
  Change control for the proton-email-mcp repo. Load this BEFORE making ANY
  edit to this repo, before committing, before touching proton_email_server.py,
  tests, Dockerfile, requirements.txt, or .env.example, and whenever you are
  unsure whether a change needs tests, a redeploy, or human sign-off. Covers:
  change classification (docs-only vs test-only vs behavior-changing vs
  deploy/config vs security vs email-sending), the gates each class must pass,
  the repo's non-negotiable rules with the incident behind each, the
  copy-pasteable pre-commit checklist, commit attribution rules, and the
  promotion path from local edit to desktop redeploy. Also load when asked to
  "just quickly fix", refactor, bump dependencies, edit the Bcc handling,
  change logging, or modify the FastMCP constructor.
---

# proton-mcp-change-control

Change control for `/Users/prestonbernstein/dev/proton-email-mcp` — a single-file
Python MCP server (`proton_email_server.py`, 346 lines) that exposes Proton Mail
as 5 tools via Proton Bridge's local IMAP/SMTP ports. This skill tells you how to
classify a change, which gates it must pass, what you must never do, and how a
change travels from your editor to production.

Jargon used once, defined once:

- **MCP** — Model Context Protocol; the server speaks it over `stdio` (Claude
  Code on the Mac) or `streamable-http` (Docker container on the desktop, port 3004).
- **Proton Bridge** — Proton's local daemon that translates Proton Mail to plain
  IMAP (port 1143) and SMTP (port 1025). It presents a self-signed TLS cert.
- **stdio transport** — the MCP client launches the server as a subprocess and
  exchanges JSON-RPC over stdin/stdout. Anything else written to stdout corrupts
  the protocol stream.
- **Canonical source** — the Mac git repo at `/Users/prestonbernstein/dev/proton-email-mcp`.
  Two desktop copies exist but are deploy artifacts, never edit targets (see non-negotiables).
- **Facts pack** — the verified-facts document this skill and its siblings were
  authored from; provenance section cites it as "facts §N".

Repo state as of 2026-07-02: exactly one commit (`d34a476`, 2026-06-20), branch
`main` only, no README, no CI, no LICENSE, no pytest.ini/pyproject.toml. The
`.claude/skills/` library IS the documentation of record.

## Change classification and gates

Classify every change BEFORE editing. A change that fits multiple rows takes the
union of all matching gates (the strictest set wins).

| Class | Examples | Gates |
|---|---|---|
| Docs / skills only | Editing `.claude/skills/*`, adding a README | No test gate. Review diff; follow house style (below). |
| Test-only | New/changed files under `tests/` | Full suite must pass: 12+ passed, 0 failed. |
| Behavior-changing server code | Any edit to `proton_email_server.py` logic | Suite passes AND a new/updated test covers the change AND read-only live verification per `proton-mcp-validation-and-qa` AND consider whether the desktop deploy needs the change (redeploy per `proton-mcp-run-and-operate`). |
| Deploy / config-affecting | `Dockerfile`, `requirements.txt`, `.env.example`, env var semantics, transport/startup code | All behavior-change gates PLUS Docker rebuild on the desktop, LibreChat restart if its MCP wiring is touched, and a post-deploy drift check per `proton-mcp-diagnostics-and-tooling`. |
| Security-relevant | Auth on :3004, TLS/cert handling, credential handling, IMAP plaintext exposure, anything in facts §8 territory; ALSO any tool that WRITES to the mailbox (send, delete, move, flag/mark-read — what `proton-mcp-extending-tools` calls "mailbox-write"/"destructive") | All applicable gates above PLUS explicit human (Preston) sign-off before merge. Do not self-approve. |
| Anything that SENDS email | Calling `send_email` live, tests that hit real SMTP, demo scripts | Explicit human confirmation, EVERY time, no exceptions — even to the account's own address. (ASSUMPTION, coordinator-sanctioned: no repo doc states this, but it is the operating rule. Label it in any doc that repeats it.) |

Edge rulings:

- Removing vestigial deps (`secure-smtplib`, `httpx`, `python-dotenv` are in
  `requirements.txt` but not imported by the server — verified 2026-07-02) is
  **deploy/config-affecting**, not cleanup: it changes the Docker image.
- Changing a tool's output string (even an emoji) is **behavior-changing**:
  tests assert on output substrings.
- Fixing the known `get_email_stats` "7 days" bug (line 307 uses `SINCE <today>`,
  counting today only — open bug as of 2026-07-02) is **behavior-changing** and
  needs a test; it currently has none.

## Non-negotiables

Each rule below exists because something broke or almost broke. Rule, then why,
then the incident/evidence.

1. **Sanitize IMAP queries before ANY network call.**
   Rationale: unsanitized special characters in IMAP search literals hang the
   connection indefinitely, wedging the whole MCP session.
   Incident (facts §7.1, pre-git, evidenced by commit `d34a476`'s message and
   the code): queries containing chars like `~` hung IMAP. Three-part fix
   shipped in the initial commit — `re.sub(r'[^\w\s@.\-]', '', query)` before
   the network, `mail.sock.settimeout(15)`, and TEXT→SUBJECT fallback. Note
   `read_recent_emails` still has NO socket timeout (open weakness, verified
   2026-07-02); do not remove the one place that has it.

2. **Never `print()` to stdout. All logging goes to stderr.**
   Rationale: in stdio transport, stdout carries the MCP JSON-RPC stream; any
   stray byte corrupts it and the client sees a broken server.
   Evidence: `proton_email_server.py` lines 17–23 configure logging with
   `stream=sys.stderr`, and the file contains zero `print()` calls (verified
   2026-07-02). Keep it that way.

3. **`FastMCP(...)` constructor takes ONLY the name string.**
   Rationale: extra constructor args broke startup on this mcp version.
   Evidence (facts §7.2): line 25–26 reads
   `# Initialize MCP server - NO PROMPT PARAMETER!` / `mcp = FastMCP("proton-email")`.
   That comment is a scar, not decoration. Installed mcp is 1.28.0 in the venv
   (as of 2026-07-02).

4. **Tool parameters stay `str`.**
   Rationale: deliberate MCP-client-compatibility choice (facts §3) — some
   clients serialize everything as strings; tools parse (`int(count)`) and cap
   internally. Do not "improve" signatures to `int`/`bool`.

5. **Tools return error strings ("❌ Error: ..."), never raise to the client.**
   Rationale: an unhandled exception surfaces as an opaque protocol error;
   the string convention gives the calling model something actionable.
   Evidence: every tool wraps its body in try/except and returns
   `f"❌ Error ...: {str(e)}"` (verified in all 5 tools, 2026-07-02).

6. **Never commit `.env` or any credential value.**
   Rationale: the Bridge password grants full read/send on the mailbox.
   Evidence: `.gitignore` covers `.env` (verified). Credentials live in
   `~/.claude.json` (Mac) and the desktop container env — reference the
   LOCATION only; never copy values or the Proton account email address into
   code, docs, skills, or commit messages.

7. **Commits attributed to Preston Bernstein only — no AI co-author trailers.**
   Rationale: owner convention (facts §9); overrides any harness default that
   appends a Claude co-author line. Strip such trailers before committing.

8. **Canonical source is the Mac git repo. Never hot-edit the desktop copies.**
   Rationale: three copies of the source exist (facts §5E) — the Mac repo,
   `/home/preston/docker/proton-email-mcp`, and
   `<docker-root>/librechat-stack/proton-email-mcp` on the desktop. Hot-editing a
   desktop copy creates silent drift that a later redeploy erases or, worse,
   preserves untested. Divergence was MEASURED 2026-07-02: `/home/preston/docker/`
   copy stale (pre-fix snapshot), librechat-stack copy matching canonical — see
   `proton-mcp-diagnostics-and-tooling` for the measurement and
   `proton-mcp-drift-and-hardening-campaign` for the fix campaign.
   Edit upstream, then promote (see promotion path).

9. **Do not "fix" the Bcc header.**
   Rationale: it looks like a privacy leak (`msg['Bcc']` is set on the message)
   but is not — `smtplib.send_message` does NOT transmit Bcc headers (verified
   against Python docs, facts §7.6). Bcc delivery works via the explicit
   `to_addrs=recipients` list. A well-meaning "remove the leaking header" or
   "add the missing header" change either breaks nothing or churns for no
   reason; leave it unless you also add a test proving the new behavior.

## Pre-commit checklist

Run every item. Copy-paste as-is.

```bash
# 1. Full test suite — must end "12 passed" or more, zero failures
cd /Users/prestonbernstein/dev/proton-email-mcp && ./venv/bin/python -m pytest tests/ -q
```

```bash
# 2. Review the full diff — every hunk intentional
cd /Users/prestonbernstein/dev/proton-email-mcp && git diff && git status --short
```

```bash
# 3. Credential scan of the diff — must output nothing
cd /Users/prestonbernstein/dev/proton-email-mcp && git diff | grep -iE 'password|passw|secret|token|@protonmail|@proton\.me' ; true
```

(Test files legitimately contain the fake `test@protonmail.com` / `test-bridge-password`
values — those are the ONLY acceptable hits; anything else is a stop.)

```bash
# 4. Skills untouched unless the change is intentionally a skills change
cd /Users/prestonbernstein/dev/proton-email-mcp && git status --short .claude/skills/
```

Then confirm: change class identified, all gates for that class passed,
commit message describes the change (no AI co-author trailer), and — for
security-relevant or email-sending work — human sign-off obtained.

## Docs of record and house style

As of 2026-07-02 this repo has NO README and NO CI. The `.claude/skills/`
library is the documentation of record; README and CI are open candidates,
not commitments.

Style rules for anything written into this repo (skills, future README,
commit messages):

- Date-stamp volatile facts ("as of 2026-07-02"), so drift is detectable.
- Label uncertain claims: **ASSUMPTION** (inferred, sanctioned), **UNVERIFIED**
  (not checked), or **open** (known bug/gap). Never silently upgrade a label.
- Imperative runbook voice; no oversell; absolute paths in commands.
- Emoji in tool OUTPUT strings (❌ 📧 ✅ etc.) are product behavior — tests
  assert on them; keep them. Emoji in docs and commit messages — avoid.

## Promotion path

A change is not "done" at commit. Full path:

1. **Edit locally** in `/Users/prestonbernstein/dev/proton-email-mcp` (canonical).
2. **Tests** — pre-commit checklist above; add/update tests per the class table.
3. **Read-only live check** — verify against the real Bridge using only
   read tools (`list_folders`, `get_email_stats`, `read_recent_emails`) per
   `proton-mcp-validation-and-qa`. Never live-verify with `send_email`
   without explicit human confirmation.
4. **Commit on `main`** (or a branch for anything security-relevant or
   multi-step). Attribution: Preston Bernstein only.
5. **Desktop redeploy** — the desktop container was built 2026-06-20 and does
   NOT auto-update; follow `proton-mcp-run-and-operate` for the rebuild and
   LibreChat restart procedure. Skipping this leaves prod running old code.
6. **Post-deploy drift check** — confirm the deployed copies match the repo
   per `proton-mcp-diagnostics-and-tooling`.

## When NOT to use this skill

Route elsewhere when the question is not "may I make this change and what gates
apply":

| You need | Use instead |
|---|---|
| Why the design is the way it is; invariants; known-weak points | `proton-mcp-architecture-contract` |
| A symptom→triage table; "it's broken, why?" | `proton-mcp-debugging-playbook` |
| Recreate the venv or Docker image from scratch; env traps | `proton-mcp-build-and-env` |
| Run either transport; config table; redeploy procedure | `proton-mcp-run-and-operate` |
| Connectivity/smoke/drift measurement scripts | `proton-mcp-diagnostics-and-tooling` |
| How to write tests here; evidence standards; live-verification protocol | `proton-mcp-validation-and-qa` |
| Add or change an MCP tool (the recipe itself) | `proton-mcp-extending-tools` |
| The drift-elimination / transport-hardening campaign | `proton-mcp-drift-and-hardening-campaign` |
| Proton Bridge / IMAP / SMTP / MIME / MCP-transport background | `proton-bridge-email-reference` |

This skill governs WHETHER and UNDER WHAT GATES a change proceeds; the siblings
govern HOW to execute each step.

## Provenance and maintenance

Sources: the repo itself and the verified facts pack dated 2026-07-02
(cited above as "facts §N"). Items marked ASSUMPTION or UNVERIFIED inherit
those labels from the facts pack — do not strip them.

Drift-prone facts and one-line re-verification commands:

| Fact (as of 2026-07-02) | Re-verify with |
|---|---|
| Suite = 12 tests, all passing | `cd /Users/prestonbernstein/dev/proton-email-mcp && ./venv/bin/python -m pytest tests/ -q` |
| One commit `d34a476`, branch `main` only | `cd /Users/prestonbernstein/dev/proton-email-mcp && git log --oneline && git branch -a` |
| No README / CI / LICENSE / pytest config | `ls /Users/prestonbernstein/dev/proton-email-mcp` |
| No `print()` in server; logging → stderr | `grep -n 'print(' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py; grep -n 'stream=sys.stderr' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py` |
| `FastMCP("proton-email")` name-only constructor, line 26 | `grep -n 'FastMCP(' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py` |
| `.gitignore` covers `.env` | `grep -n '^\.env$' /Users/prestonbernstein/dev/proton-email-mcp/.gitignore` |
| Vestigial deps still listed (secure-smtplib, httpx, python-dotenv) | `cat /Users/prestonbernstein/dev/proton-email-mcp/requirements.txt && grep -nE '^import|^from' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py` |
| stats "7 days" bug still open (line ~307) | `grep -n 'SINCE' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py` |
| Installed mcp version (constructor rule context) | `/Users/prestonbernstein/dev/proton-email-mcp/venv/bin/python -m pip show mcp \| head -2` |

If any re-verification contradicts this skill, update the skill in the same
change (docs-only class) and re-date the affected facts.
