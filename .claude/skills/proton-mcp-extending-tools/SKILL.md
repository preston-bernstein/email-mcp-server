---
name: proton-mcp-extending-tools
description: Recipe for adding or changing MCP tools in proton-email-mcp. Load when adding a new MCP tool, changing a tool's signature or behavior, or planning candidate features such as attachments, mark-read/unread, move/delete, pagination, HTML body extraction, or the get_email_stats 7-day fix. Covers the house tool anatomy (all-str params, "❌ Error:" strings, per-call IMAP connect), new-code standards, the add-a-tool checklist, gating classes, and the candidate roadmap.
---

# Extending proton-email-mcp: adding or changing tools

This skill is the recipe for writing a new `@mcp.tool()` in `proton_email_server.py`, or changing an existing one, without breaking the house conventions that keep this server boring and reliable. The whole server is one 346-line file; every tool follows the same shape. Copy the shape.

Jargon, defined once:
- **MCP** — Model Context Protocol; this server exposes Proton Mail as MCP tools to clients like Claude Code and LibreChat.
- **Proton Bridge** — the local daemon that translates plain IMAP/SMTP (ports 1143/1025) into Proton's encrypted API. The server talks ONLY to Bridge, never to Proton directly.
- **FastMCP** — the framework object (`mcp = FastMCP("proton-email")`, line 26). The `@mcp.tool()` decorator registers a function as an MCP tool automatically.
- **stdio vs streamable-http** — the two transports (lines 333–343): stdio for the Mac Claude Code deployment, streamable-http (uvicorn, port 3004) for the desktop Docker deployment.

Line references below are to `proton_email_server.py` and `tests/test_proton_email_server.py` as of 2026-07-02 (single commit `d34a476`). Re-verify line numbers if the file has been edited since.

## Anatomy of a tool here

Read `read_recent_emails` (lines 72–126) and `search_emails` (lines 128–200) before writing anything. `search_emails` is the better exemplar — it is the only tool that got the full post-incident hardening. Every tool follows this sequence:

| # | Step | Exemplar lines | Rule |
|---|------|----------------|------|
| 1 | `@mcp.tool()` on an `async def` returning `str` | 72–73, 128–129 | Decorator auto-registers. In mcp 1.28.0 it returns the original function unchanged, which is why tests import the module and call tools directly. |
| 2 | ALL params are `str` with string defaults | 73: `count: str = "10"`; 129: `max_results: str = "20"` | House convention for MCP client compatibility. Do NOT switch to `int` — parse inside the function (`limit = int(count) if count.strip() else 10`, line 81) and catch `ValueError` (lines 122–123, 196–197). |
| 3 | Log intent via `logger` (stderr) | 75, 131 | `logging` is configured to stderr (lines 17–23). NEVER `print()` — stdout is the stdio protocol channel; writing to it corrupts the MCP session. |
| 4 | Credentials check FIRST, return `"❌ Error: ..."` string | 77–78, 136–137 | Tools never raise to the client; every failure path returns a human-readable string starting with `❌ Error:`. |
| 5 | Parse / validate / sanitize inputs BEFORE any network call | 133–143 | `search_emails` rejects empty query, sanitizes with `re.sub(r'[^\w\s@.\-]', '', query)` (line 141), and rejects empty-after-sanitize — all before `IMAP4()` is constructed. Test `test_search_empty_after_sanitization` (tests lines 63–69) asserts IMAP is never called. |
| 6 | Fresh `imaplib.IMAP4(PROTON_BRIDGE_HOST, PROTON_BRIDGE_IMAP_PORT)` + `login` per call | 85–87, 150–152 | No connection pooling, no shared state. One connect/login/logout per tool invocation. Plain `IMAP4` (not `IMAP4_SSL`) — Bridge speaks plain IMAP locally. |
| 7 | Check select status where folder is user-supplied | 153–156 | `search_emails` checks `status != "OK"` and returns a folder error. (`read_recent_emails` line 87 skips this check — legacy; new code must check.) |
| 8 | Cap result counts at 50 | 82–83, 147–148 | Hard safety limit. Keep it in any new listing tool. |
| 9 | Return a human-readable string, most recent first | 100–120, 176–194 | Reverse the ID slice (`recent_ids.reverse()`, line 98); truncate body previews to 300 chars (line 116). |
| 10 | `mail.logout()` before returning | 93/119, 169/193 | Legacy code calls logout on success paths only — see New-code standards below for the required improvement. |

Module-level config (lines 29–33) is read from the process environment at import time — `PROTON_USERNAME`, `PROTON_PASSWORD`, `PROTON_BRIDGE_HOST`, `PROTON_BRIDGE_IMAP_PORT`, `PROTON_BRIDGE_SMTP_PORT`. The server never reads `.env` files. Never write credential values or the Proton account email address into code, tests, docs, or skills.

For SMTP tools, the exemplar is `send_email` (lines 202–250): `MIMEMultipart` + `MIMEText(..., 'plain')`, `smtplib.SMTP` in a `with` block (context manager handles quit), `starttls` with a context that sets `check_hostname = False` and `verify_mode = CERT_NONE` (lines 229–232) because Bridge presents a self-signed certificate. Do not "fix" the cert settings ad hoc — TLS posture is owned by the hardening campaign (see Fenced-off wrong paths).

## New-code standards (stricter than legacy)

These are improvements over the existing code. NEW code must meet them. EXISTING code is grandfathered until the hardening campaign (`proton-mcp-drift-and-hardening-campaign`) sweeps it — do not drive-by "fix" legacy tools while adding a feature; that mixes gating classes.

1. **Socket timeout on every new IMAP connection.** Immediately after constructing the connection:
   ```python
   mail = imaplib.IMAP4(PROTON_BRIDGE_HOST, PROTON_BRIDGE_IMAP_PORT)
   mail.sock.settimeout(15)  # prevent indefinite hang
   ```
   `search_emails` does this (line 151) because a special-character query once hung the connection indefinitely (pre-git incident, recorded in commit `d34a476`'s message). `read_recent_emails` (line 85), `list_folders` (line 261), and `get_email_stats` (line 294) still lack it — known-weak, on the roadmap. Add a test asserting `m.sock.settimeout.assert_called_once_with(15)` like tests lines 107–112.

2. **`logout` in `try/finally`.** Legacy tools call `mail.logout()` on success paths only, so connections leak on exception paths. New code:
   ```python
   mail = imaplib.IMAP4(PROTON_BRIDGE_HOST, PROTON_BRIDGE_IMAP_PORT)
   mail.sock.settimeout(15)
   try:
       mail.login(PROTON_USERNAME, PROTON_PASSWORD)
       ...  # work
       return result
   finally:
       try:
           mail.logout()
       except Exception:
           pass  # already returning a result or propagating to the outer handler
   ```

3. **Sanitize every user-supplied IMAP search literal** with the existing pattern before any network call:
   ```python
   import re
   safe = re.sub(r'[^\w\s@.\-]', '', user_value).strip()
   if not safe:
       return f"❌ Error: '{user_value}' contains no searchable characters"
   ```
   This is the exact hang-incident fix (line 141). It intentionally strips quotes, so quoted-phrase search is unsupported by design — see Fenced-off wrong paths before loosening it.

## Checklist: adding a tool

Work through this in order. Steps 1–4 are mechanical; steps 6–7 are where changes get lost.

1. **Write the function** in `proton_email_server.py`, in the `# === MCP TOOLS ===` section (after line 70), following the anatomy table and new-code standards above. `@mcp.tool()`, `async def`, all-`str` params with string defaults, returns `str`.

2. **Write tests first or alongside** in `tests/test_proton_email_server.py`, following the patterns there (full anatomy in the sibling skill `proton-mcp-validation-and-qa`):
   - Env vars are injected via `os.environ.setdefault(...)` BEFORE `import proton_email_server` (tests lines 13–21). The module reads env at import time — a test file that imports first and sets env second gets empty credentials. Do not reorder.
   - Mock `imaplib.IMAP4` / `smtplib.SMTP` with `unittest.mock.patch`; reuse the `mock_imap()` helper (tests lines 36–46) which pre-wires `select`/`search`/`fetch`/`list` responses and a mock `sock`.
   - Mark every test `@pytest.mark.asyncio` (pytest-asyncio strict/marker mode; there is no pytest config file) and `await` the tool directly — the decorator returns the original function unchanged, so `await srv.my_tool(...)` works.
   - Cover at minimum: happy path, missing credentials or missing required param (`❌` in result), input validation rejects before IMAP is constructed (`MockIMAP.assert_not_called()`), and `settimeout(15)` asserted.

3. **Run the suite.**
   ```bash
   cd /Users/prestonbernstein/dev/proton-email-mcp && ./venv/bin/python -m pytest tests/ -q
   ```
   Baseline as of 2026-07-02: `12 passed in 0.22s`. All green, no live Bridge required.

4. **Register nothing manually.** The `@mcp.tool()` decorator is the registration. There is no tool list, no schema file, no router to update.

5. **Verify the tool appears** — depends on transport (procedures owned by `proton-mcp-run-and-operate`; summary):
   - **stdio (Mac / Claude Code):** restart the Claude Code session; the tool shows up as `mcp__proton-mail__<name>`.
   - **streamable-http (desktop Docker):** the running container was built from an image dated 2026-06-20 — editing the Mac repo changes NOTHING in prod. You must rebuild the Docker image, restart the container, and restart LibreChat so it re-handshakes the MCP server. Beware source drift: two desktop copies of the source exist besides the Mac repo (`/home/preston/docker/proton-email-mcp`, `/opt/docker/librechat-stack/proton-email-mcp`); the Mac git repo is canonical — edit there first, then propagate.

6. **Classify and gate the change** per `proton-mcp-change-control`. Anything that WRITES to the mailbox — send, delete, move, flag/mark-read — is security-relevant and needs explicit human sign-off before merge or deploy (ASSUMPTION, coordinator-sanctioned; no repo doc states it, but treat it as binding). Read-only tools still go through change control, at a lower gate.

7. **Never live-test `send_email`-class tools against real recipients.** Dev/test sends go only to the account's own address, with explicit human approval first (ASSUMPTION, coordinator-sanctioned).

## Candidate roadmap

All items below are CANDIDATES — open, none started, nothing committed, as of 2026-07-02. Each entry: what it needs technically + its gating class. The shorthand here is read-only < behavior-affecting < mailbox-write < destructive; it maps onto `proton-mcp-change-control`'s classification as follows: "behavior-affecting" = its "behavior-changing server code" class; "mailbox-write" and "destructive" both fall under its "security-relevant" class (explicit human sign-off) — destructive additionally warrants a trash-folder-first design. That skill's table is the authority on gates.

| Candidate | Technical shape | Gate |
|---|---|---|
| Fix `get_email_stats` 7-day bug | Line 307 uses `SINCE datetime.now().strftime("%d-%b-%Y")` — that counts TODAY only, but line 315 labels it "Recent emails (7 days)". Verified open bug. Fix: `(datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")` (IMAP SINCE requires the `%d-%b-%Y` format, e.g. `25-Jun-2026`). Write a regression test FIRST that asserts the date passed to `mail.search` is 7 days back. | Behavior-affecting (read-only output changes) |
| Socket timeout in `read_recent_emails` (and `list_folders`, `get_email_stats`) | Add `mail.sock.settimeout(15)` after each `IMAP4(...)` construction (lines 85, 261, 294); test per tests lines 107–112. | Behavior-affecting, low risk |
| `try/finally` logout everywhere | Restructure each tool per New-code standards item 2; assert `logout` called on exception paths in tests. | Behavior-affecting, low risk |
| Attachments (read side) | Extend `extract_email_body`-style MIME walk (lines 50–68) to collect parts where `Content-Disposition` contains `attachment`; enforce size limits before decode; decide WHERE decoded files are written (no location exists today — needs a design decision; scratch dir vs. return-as-base64 both have problems). | Behavior-affecting; file-writing raises the gate |
| Mark read/unread | IMAP `STORE <id> +FLAGS (\Seen)` / `-FLAGS (\Seen)`. This is a mailbox WRITE — first write-class IMAP operation in the server. High gate: human sign-off required. | Mailbox-write |
| Move / delete | Move = `COPY` to target folder + `STORE +FLAGS (\Deleted)` + `EXPUNGE` on source; delete = `STORE +FLAGS (\Deleted)` + `EXPUNGE`. `EXPUNGE` is irreversible from the client's view. Highest gate: destructive, human sign-off, and strongly prefer a trash-folder move over expunge as the default. | Destructive |
| Pagination | IMAP sequence sets (e.g. fetch a range instead of `mail_ids[-limit:]`). SUBTLETY: the server uses SEQUENCE NUMBERS, not UIDs — sequence numbers renumber whenever messages are expunged, so page 2 can shift between calls. A migration to `mail.uid('search', ...)` / `mail.uid('fetch', ...)` is the safer foundation for pagination; flag this in the design before building. | Behavior-affecting |
| HTML body extraction improvements | `extract_email_body` (lines 50–68) concatenates text/plain AND text/html parts raw — HTML arrives as tag soup in the 300-char preview. Candidate: prefer text/plain part when present; strip tags otherwise. | Behavior-affecting |
| TLS hardening (IMAP STARTTLS, SMTP cert verification) | OWNED BY `proton-mcp-drift-and-hardening-campaign`. Do not design or implement it from this skill — cross-reference only. Whether this Bridge build accepts STARTTLS on 1143 is UNVERIFIED. | Campaign-gated |

## Fenced-off wrong paths

Things that look like improvements and are not. Do not do these without reading the cited context.

- **Do not add constructor arguments to `FastMCP`.** Line 25's comment "NO PROMPT PARAMETER!" records a real incident: passing extra kwargs (e.g. a prompt/instructions argument) to `FastMCP(...)` broke startup on this mcp version. Keep line 26 as exactly `mcp = FastMCP("proton-email")`.
- **Do not "fix" Bcc handling in `send_email`.** Setting `msg['Bcc']` (line 223) looks like a privacy leak; it is not — `smtplib.send_message` (line 244) does NOT transmit the Bcc header (verified against Python docs). BCC delivery works via the explicit `to_addrs` recipient list (lines 238–244). A "fix" that deletes the header or restructures recipients risks breaking working behavior for a non-bug.
- **Do not loosen the sanitizer to support quoted phrases** (or any other rejected character) without redoing the hang-safety analysis. The regex on line 141 exists because unsanitized characters hung the IMAP connection indefinitely (pre-git incident). Loosening it is a campaign-class change — route to `proton-mcp-drift-and-hardening-campaign` / `proton-mcp-change-control`, and keep the `settimeout(15)` + TEXT→SUBJECT fallback (lines 158–164) defense layers regardless.
- **Do not call the Proton REST API directly.** Proton Bridge's local IMAP/SMTP endpoints are the contract; the server has no Proton API credentials, no session handling, and adding them changes the entire security model. All mail I/O goes through Bridge.
- **Do not use `print()` anywhere in the server.** stdout is the stdio MCP transport. Use `logger.info/error` (stderr) only.
- **Do not change tool params to non-`str` types.** The all-`str` signature is a deliberate MCP-client-compat choice, not an oversight.

## When NOT to use this skill

| Your task | Use instead |
|---|---|
| Understand why the design is the way it is; invariants; known-weak points | `proton-mcp-architecture-contract` |
| Classify/gate/review a change; what needs human sign-off | `proton-mcp-change-control` |
| Something is broken; symptom → cause triage | `proton-mcp-debugging-playbook` |
| Recreate the venv or Docker image; env var traps | `proton-mcp-build-and-env` |
| Run either transport; deploy/redeploy; config table | `proton-mcp-run-and-operate` |
| Connectivity checks, smoke tests, drift measurement scripts | `proton-mcp-diagnostics-and-tooling` |
| Test-writing standards in depth; live-verification protocol | `proton-mcp-validation-and-qa` |
| TLS hardening, killing source drift, sanitizer loosening | `proton-mcp-drift-and-hardening-campaign` |
| IMAP/SMTP/MIME/Bridge/MCP-transport background knowledge | `proton-bridge-email-reference` |

## Provenance and maintenance

- Authored 2026-07-02 from: `proton_email_server.py` (346 lines, commit `d34a476`, the repo's only commit), `tests/test_proton_email_server.py` (12 tests), and the verified facts pack of 2026-07-02. Test suite run at authoring time: `12 passed`.
- All line numbers reference commit `d34a476`. Any edit to `proton_email_server.py` can shift them — re-verify with a quick read before trusting a line ref, and update this file when a roadmap candidate lands (move it out of the roadmap table; update the anatomy if conventions changed).
- Labeled uncertainty: mailbox-write sign-off gate and the no-real-recipients test rule are ASSUMPTION (coordinator-sanctioned, no repo doc); STARTTLS-on-1143 support is UNVERIFIED; every roadmap item is an open candidate, not a commitment.
- Volatile facts (deployment topology, desktop source copies, image build date) were true 2026-07-02 — cross-check against `proton-mcp-run-and-operate` before acting on them.
- Maintainer: whoever changes tool-authoring conventions (param typing, error format, connection pattern) MUST update the anatomy table here in the same change.
