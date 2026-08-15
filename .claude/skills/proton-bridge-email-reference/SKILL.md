---
name: proton-bridge-email-reference
description: Domain-theory reference pack for proton-email-mcp. Load when you need to understand Proton Bridge (app passwords, self-signed cert, local IMAP/SMTP re-exposure), IMAP search syntax and semantics (TEXT vs SUBJECT, SINCE date format, why special characters hang naive SEARCH), sequence numbers vs UIDs, MIME/RFC 2047 header decoding, multipart body extraction, SMTP STARTTLS vs SMTPS, the Bcc/send_message envelope subtlety, or MCP transport concepts (stdio vs streamable-http, FastMCP, stdout purity) while working on this repo. Explains WHY the code is the way it is; not a how-to-run or how-to-debug guide.
---

# Proton Bridge Email Reference

Domain theory behind `proton_email_server.py` — Proton Bridge, IMAP, MIME, SMTP, and MCP concepts, **as they apply to this codebase**. Every concept here is anchored to a line in `proton_email_server.py` (346 lines, single file, repo root) or to the verified deployment facts. This is a knowledge pack, not a textbook: if a protocol feature doesn't touch this repo, it isn't here.

Line numbers refer to `proton_email_server.py` at commit `d34a476` (the only commit as of 2026-07-02). If the file has been edited since, treat line refs as approximate anchors and re-locate by the quoted code.

---

## 1. The Proton Bridge model

**The problem Bridge solves.** Proton Mail is end-to-end encrypted: messages are stored encrypted on Proton's servers, and Proton (deliberately) cannot expose a standard IMAP/SMTP interface to them — a plain mail client would just see ciphertext. So there is no `imap.proton.me` you can point `imaplib` at.

**The solution.** Proton Bridge is a local daemon that:

1. Logs into the Proton account itself (it holds the real account session),
2. Downloads and **decrypts mail locally**, and
3. Re-exposes the mailbox as **standard, boring IMAP and SMTP** on local ports — here **IMAP on 1143** and **SMTP on 1025**.

Everything downstream of Bridge (including this entire MCP server) speaks ordinary IMAP/SMTP and knows nothing about Proton's encryption. That is why `proton_email_server.py` imports only stdlib `imaplib` (line 8) and `smtplib` (line 9) — no Proton SDK exists or is needed.

**Where Bridge runs here** (facts pack §5C, verified 2026-07-02): as a Docker container `protonmail-bridge` on the desktop (10.0.0.243), defined in `/opt/docker/auth-stack/docker-compose.yml`, publishing `1143:143` (IMAP) and `1025:25` (SMTP), with its state/auth in the volume `./data/protonmail-bridge:/root`. The MCP server reaches it via `PROTON_BRIDGE_HOST` / `PROTON_BRIDGE_IMAP_PORT` / `PROTON_BRIDGE_SMTP_PORT` (lines 31–33; defaults `127.0.0.1`, `1143`, `1025`).

### 1.1 App password, not account password — the #1 login confusion

Bridge generates its **own app password** for local clients. When `mail.login(PROTON_USERNAME, PROTON_PASSWORD)` runs (line 86 and equivalents), `PROTON_PASSWORD` must be the **Bridge-generated app password**, never the real Proton account password. The account password only works against Proton's own web/API login — Bridge will reject it on 1143/1025.

Consequences for this repo:

| Symptom | Likely cause |
|---|---|
| `LOGIN failed` / auth error from a tool | `PROTON_PASSWORD` is the account password, or Bridge regenerated its app password |
| All tools return `❌ Error: Proton credentials not configured` | `PROTON_USERNAME`/`PROTON_PASSWORD` empty in the process env (checked first in every tool, e.g. lines 77–78) — note the server never reads `.env` files; env must come from the actual process environment |
| Login worked yesterday, fails today | Bridge lost its Proton session |

If Bridge loses its Proton session, **re-authentication happens inside the Bridge container** (its state volume above) — nothing in this repo can fix it. Route that to `proton-mcp-run-and-operate` / `proton-mcp-debugging-playbook`.

Credential values live in `~/.claude.json` (Mac) and the desktop container env. Never copy the values, or the Proton account email address, into any file.

### 1.2 The self-signed certificate — why `CERT_NONE` exists

Bridge presents a **self-signed TLS certificate** (it's a local daemon; no public CA will sign a cert for `localhost`/a LAN IP). A default Python SSL context would refuse the STARTTLS handshake with a verification error. That is the entire reason for lines 229–232 in `send_email`:

```python
import ssl as _ssl
_ctx = _ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = _ssl.CERT_NONE
```

This is a deliberate, documented trade-off (facts pack §8): the SMTP channel is **encrypted but the server's identity is unauthenticated**. It is acceptable only because the peer is a Bridge on the owner's own machine/LAN. Do not "fix" it by re-enabling verification without a plan for trusting Bridge's cert — and do not copy this pattern to any public-internet endpoint. Whether it's still acceptable is architecture-contract territory (`proton-mcp-architecture-contract`).

---

## 2. IMAP as used here

**IMAP** (Internet Message Access Protocol) is the read side: a stateful, line-oriented text protocol where the client selects a mailbox ("folder") and issues commands against it. This server uses exactly six commands, via Python's `imaplib`:

| IMAP command | `imaplib` call | Where in this file |
|---|---|---|
| LOGIN | `mail.login(user, pw)` | 86, 152, 262, 295 |
| SELECT | `mail.select(folder)` | 87, 153, 296 |
| SEARCH | `mail.search(None, ...)` | 89, 160/164, 299/303/307 |
| FETCH | `mail.fetch(id, "(RFC822)")` | 103, 179 |
| LIST | `mail.list()` | 264 |
| LOGOUT | `mail.logout()` | success paths of every tool |

### 2.1 Connection model

Every tool opens a **fresh plaintext connection** with `imaplib.IMAP4(PROTON_BRIDGE_HOST, PROTON_BRIDGE_IMAP_PORT)` (lines 85, 150, 261, 294). `IMAP4` (as opposed to `IMAP4_SSL`) means no TLS at all: from the Mac deployment, the Bridge app password crosses the LAN **unencrypted** on port 1143 (facts pack §8, known-weak). Bridge's 1143 is plain IMAP; whether this Bridge build also accepts STARTTLS on 1143 is **UNVERIFIED** (as of 2026-07-02).

Two asymmetries to know:

- **Timeout:** only `search_emails` sets `mail.sock.settimeout(15)` (line 151, comment: "prevent indefinite hang on malformed queries"). `read_recent_emails`, `list_folders`, and `get_email_stats` have **no socket timeout** — a stalled Bridge hangs them indefinitely (facts pack §3.1, known risk).
- **Cleanup:** `mail.logout()` is called on success paths only, never in a `finally` — connections leak on exception paths (facts pack §8).

### 2.2 The SEARCH criteria this server uses

IMAP SEARCH takes space-separated **criteria**; the server responds with matching message numbers. This codebase uses exactly four:

| Criterion | Meaning (standard IMAP semantics) | Used at |
|---|---|---|
| `ALL` | Every message in the selected folder | 89 (`read_recent_emails`), 299 (`get_email_stats` total) |
| `UNSEEN` | Messages without the `\Seen` flag, i.e. unread | 303 (`get_email_stats` unread) |
| `SINCE <date>` | Messages whose **internal date** (server arrival date, not the Date: header) is on or after `<date>` | 307 (`get_email_stats`) |
| `TEXT <string>` / `SUBJECT <string>` | `TEXT` matches the string in headers **and** body; `SUBJECT` matches the Subject header only | 160 / 164 (`search_emails`) |

**SINCE date format.** IMAP dates are `dd-Mon-yyyy`, e.g. `02-Jul-2026` — produced at line 307 by `datetime.now().strftime("%d-%b-%Y")`. Two things to know:

- `%b` is **locale-dependent** in principle; it works here because the deployments run in C/English locales. If you ever see `SEARCH` errors mentioning a date, check locale.
- **Open bug (verified line 307, still open as of 2026-07-02):** the code searches `SINCE <today>` but labels the result "Recent emails (7 days)" (line 315). It counts *today's* mail only. This is a documented open bug — do not describe it as fixed, and if you fix it, route through `proton-mcp-change-control`.

**TEXT vs SUBJECT and the fallback (lines 158–164).** `search_emails` tries `TEXT` first (broadest useful match: subject + other headers + body). If the search raises or returns non-OK, it falls back to `SUBJECT`-only. The fallback exists because a Bridge/server may reject or choke on `TEXT` (part of the pre-git hang fix, facts pack §7.1). Practical consequence: when the fallback fires, results silently narrow from "anywhere in the message" to "subject line only" — the user is not told which path ran.

### 2.3 Why special characters hang naive SEARCH — the sanitizer

This is the most important piece of IMAP theory encoded in this file.

IMAP search strings may only be sent bare if they are **atoms** — roughly, runs of ordinary characters with no spaces, quotes, parens, brackets, `%`, `*`, backslash, control chars, or non-ASCII. Anything else must be sent as a **quoted string** or, for the general case, as a **literal**: the client sends `{<byte-count>}` + CRLF, waits for the server's `+` continuation response, then sends the raw bytes. That's a mid-command **handshake**.

`imaplib.search()` does not do any of this for you — it splices your criteria into the command line mostly as-is. Inject a non-atom character (e.g. `~`, `"`, `(`) raw, and the server parses the command differently than the client thinks it sent it. Client and server are now **desynchronized**: each side waits for the other, and the connection hangs until something times out.

That is exactly the pre-git incident recorded in the initial commit message ("Fixes: IMAP query sanitization, socket timeout, TEXT/SUBJECT fallback" — facts pack §7.1). The three-part fix, all in `search_emails`:

1. **Sanitize before the network** (line 141): `re.sub(r'[^\w\s@.\-]', '', query)` — strip everything except word chars, whitespace, `@`, `.`, `-`. The allowlist is deliberately narrow (email addresses and plain words survive; `~`, quotes, parens do not). If the query is empty after sanitizing, return an error without connecting (line 143).
2. **Socket timeout** `settimeout(15)` (line 151) — belt-and-braces if a hang still occurs.
3. **TEXT → SUBJECT fallback** (lines 159–164) — degrade rather than fail.

Worked example: user query `budget (Q3) ~draft` → sanitized to `budget Q3 draft` → sent as `SEARCH TEXT budget Q3 draft`. Note the subtlety: multiple words in `TEXT` criteria are, per IMAP grammar, parsed as separate criteria keys/values — imaplib passes them through, and behavior on multi-word unquoted strings is server-dependent. The sanitizer prevents hangs; it does not guarantee multi-word queries mean "this exact phrase". Keep search queries to single words/addresses for predictable results.

If you loosen the sanitizer, the correct general fix is proper IMAP quoting/literals — a change-control matter (`proton-mcp-extending-tools` has the recipe context).

### 2.4 Sequence numbers vs UIDs

IMAP has two ways to identify a message inside a folder:

| | Sequence number | UID |
|---|---|---|
| What | Position in the mailbox, 1..N | Stable per-message identifier |
| Stability | **Shifts when any earlier message is expunged/deleted** | Stable for the life of the folder (until UIDVALIDITY changes) |
| How requested | `mail.search(None, ...)` / `mail.fetch(...)` | `mail.uid('search', ...)` / `mail.uid('fetch', ...)` |

**This server uses sequence numbers exclusively** — every `mail.search`/`mail.fetch` call (lines 89/103, 160/179, 299/303/307) is the non-UID form. That is fine for the current read-and-return pattern: search and fetch happen on the *same connection within milliseconds*, so the numbering can't shift underneath us, and the numbers are never handed to the client for later use.

It becomes **dangerous** the moment a future tool does delete/move/flag operations, or stores an ID for use on a *different* connection: a message deleted by another client in between renumbers everything, and you operate on the wrong email. Any such feature must switch to UIDs — see `proton-mcp-extending-tools` before adding one.

Related safety valve: `mail.select(folder, readonly=True)` opens a folder read-only, so the session cannot alter flags or content. The server uses plain writable `select` (lines 87/153/296), which means its `RFC822` fetches mark fetched messages `\Seen` (an unmarked side effect of reading mail through these tools: previews mark mail as read). The diagnostics smoke script uses `readonly=True` (see `proton-mcp-diagnostics-and-tooling`) — prefer it for ad-hoc inspection so probing doesn't mark mail read.

### 2.5 LIST parsing (lines 264–277)

`mail.list()` returns lines like `(\HasNoChildren) "/" "Folders/Receipts"` — flags, hierarchy delimiter, then the quoted folder name. `list_folders` parses this by splitting on `"` and taking `parts[-2]` (lines 274–276), i.e. the content of the **last quoted segment**. This works for Bridge's output but is a heuristic, not a parser: a folder name containing `"` or an unquoted-atom folder name would mis-parse. Known simplification, fine as-is.

---

## 3. MIME and header decoding as used here

**MIME** (Multipurpose Internet Mail Extensions) is how email carries structure: a message is either a single part or a `multipart/*` tree of parts, each with its own `Content-Type` and charset.

### 3.1 From raw bytes to a message object

`mail.fetch(mail_id, "(RFC822)")` (lines 103, 179) asks for the **entire raw message** — headers + body, as originally transmitted, as bytes. `email.message_from_bytes(raw_email)` (lines 105, 181) parses those bytes into an `email.message.Message` tree. RFC 822 is the ancestral message-format spec; in IMAP, `RFC822` is simply the fetch item meaning "the whole thing".

### 3.2 Body extraction — `extract_email_body` (lines 50–68)

Real-world messages are usually `multipart/alternative` (plain + HTML versions) or `multipart/mixed` (body + attachments). The walk logic:

```python
if message.is_multipart():
    for part in message.walk():                     # depth-first over all parts
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition"))
        if content_type in ["text/plain", "text/html"] and "attachment" not in content_disposition:
            payload = part.get_payload(decode=True) # undoes base64/quoted-printable
            charset = part.get_content_charset()
            if payload:
                body += payload.decode(charset or "utf-8", errors="replace")
```

Decisions encoded here, and what they mean:

- **`get_payload(decode=True)`** reverses the *transfer encoding* (base64 or quoted-printable — how binary-unsafe SMTP lines carry arbitrary bytes). You then still must decode the resulting bytes with the part's declared **charset**.
- **Attachment skip:** a `Content-Disposition: attachment` header marks a part as a file, not body text. The `"attachment" not in content_disposition` check skips those, so a PDF named in the disposition doesn't get decoded into the preview.
- **Charset fallback:** `charset or "utf-8"` plus `errors="replace"` means a missing/wrong charset degrades to `�` characters instead of raising — the tool never crashes on a badly-encoded message.
- **Known behavior, not a bug:** for `multipart/alternative`, both the text/plain AND text/html versions match and get **concatenated** — body previews can contain the same content twice (once as text, once as raw HTML markup). `read_recent_emails` truncates the preview to 300 chars (line 116), which usually masks this.

### 3.3 Header decoding — `decode_email_header` (lines 37–48)

Headers are ASCII-only by spec, so non-ASCII header values (subjects, display names) arrive **RFC 2047-encoded**: `=?<charset>?<B|Q>?<encoded-text>?=`, e.g.

```
Subject: =?utf-8?B?UmVjaGVudW5nIMOcYmVyd2Vpc3VuZw==?=
```

`email.header.decode_header()` (line 41) parses a header into a list of `(bytes-or-str, charset)` chunks — a single header can mix encoded and plain segments. The function stitches them back together, decoding byte chunks with the declared charset (falling back to utf-8, `errors="replace"`, line 45). `None`/missing headers return `"Unknown"` (lines 39–40). Used for `Subject` and `From` in both read and search tools (lines 107–108, 183–184); `Date` is taken raw (lines 109, 185) since it's ASCII by construction.

---

## 4. SMTP as used here

**SMTP** (Simple Mail Transfer Protocol) is the send side. `send_email` is the only tool that uses it (lines 202–250).

### 4.1 Plain connect, then STARTTLS — not SMTPS

```python
with smtplib.SMTP(PROTON_BRIDGE_HOST, PROTON_BRIDGE_SMTP_PORT) as server:  # line 233
    server.starttls(context=_ctx)                                          # line 234
    server.login(PROTON_USERNAME, PROTON_PASSWORD)                         # line 235
```

Two ways exist to get TLS on SMTP:

| Mode | How | Typical port |
|---|---|---|
| **SMTPS** (implicit TLS) | TLS from the first byte; `smtplib.SMTP_SSL` | 465 |
| **STARTTLS** (explicit upgrade) | Connect plaintext, issue `STARTTLS`, upgrade the same socket to TLS, then authenticate | 587 (or here, Bridge's 1025) |

This code uses STARTTLS because that is what Bridge offers on its SMTP port — Bridge's 1025 is a plaintext-greeting SMTP endpoint that upgrades on request; it is not an implicit-TLS port, so `SMTP_SSL` would fail the handshake. Crucially, `login()` happens **after** the upgrade, so the app password is not sent in cleartext on this channel (contrast with the IMAP side, §2.1). The `_ctx` with `CERT_NONE` is the self-signed-cert accommodation explained in §1.2.

### 4.2 Envelope vs headers — and why the Bcc here is NOT a leak

An email has two independent recipient notions:

- **Envelope recipients**: the `RCPT TO:` addresses given to the SMTP server — these determine actual delivery.
- **Header recipients**: the `To:`/`Cc:`/`Bcc:` lines inside the message — these are just text everyone can read.

Bcc works precisely by putting an address in the *envelope* but **not** in the headers that get transmitted.

This code sets `msg['Bcc']` (line 223) — which looks like a classic leak (a transmitted Bcc header shows every recipient who was blind-copied). It is **not** a leak here, and this is fenced: **do not "fix" it.** `smtplib.send_message()` (line 244) is documented to strip `Bcc` (and `Resent-Bcc`) headers before transmission; it also uses them to *compute* envelope recipients when `to_addrs` is not given. Verified against Python docs (facts pack §7.6). So the header serves only as local bookkeeping and is never sent.

Envelope assembly is explicit anyway (lines 238–242): `recipients = [to] + cc.split(',') + bcc.split(',')` (each entry stripped), passed as `to_addrs=recipients` — which **overrides** any header-derived recipient computation. Consequences of the comma-split convention:

- `to_email` is treated as a **single address** (never split); `cc`/`bcc` are comma-separated lists.
- A display-name form like `"Doe, Jane" <jane@x>` in cc/bcc would be split at the comma and break — pass bare addresses.

The message itself is `MIMEMultipart` with a single `MIMEText(body, 'plain')` part (lines 215, 226): plaintext-only sends, no HTML, no attachments (extending that is `proton-mcp-extending-tools` territory).

Discipline reminder (facts pack §9): sending real email from agent contexts requires explicit human confirmation; in dev/test, only to the account's own address with approval.

---

## 5. MCP concepts as used here

**MCP (Model Context Protocol)** is the protocol that lets LLM clients (Claude Code, Claude Desktop, LibreChat, ...) discover a server's typed **tools** and call them with JSON arguments over a transport, using JSON-RPC messages. This repo is one MCP server exposing 5 email tools.

### 5.1 FastMCP and the decorator

`FastMCP` (from `mcp.server.fastmcp`, the `mcp[cli]` package — installed version 1.28.0 in the venv) is the decorator-based server framework:

```python
mcp = FastMCP("proton-email")   # line 26
@mcp.tool()                     # lines 72, 128, 202, 252, 285
async def read_recent_emails(count: str = "10", folder: str = "INBOX") -> str: ...
```

- The constructor takes the server name string and **nothing else** — the line-26 comment `NO PROMPT PARAMETER!` records a past startup failure from passing an extra kwarg (facts pack §7.2). Keep it to the name.
- `@mcp.tool()` reads the function's **signature and docstring** to generate the tool's JSON schema — parameter names, types, defaults, and the description the LLM sees all come from the Python declaration.
- In mcp 1.28.0, `@mcp.tool()` registers the tool **and returns the original function unchanged** (verified, facts pack §3). That is why `tests/test_proton_email_server.py` simply imports the module and `await`s the tools directly — no MCP client machinery needed in tests.

**Why every parameter is `str`** — including `count`, `max_results`: a deliberate house convention for maximum client compatibility. Some MCP clients serialize numbers as JSON strings; declaring `int` makes those calls fail schema validation before the tool ever runs. Declaring `str` and converting inside (`int(count)`, line 81, with a `ValueError` catch at line 122 returning a friendly error) accepts everything. Keep this convention for any new tool.

Also note the tool error convention: tools **return** human-readable strings starting with `❌ Error:` rather than raising — an exception would surface as an opaque protocol-level error to the client; a returned string is something the LLM can read and act on.

### 5.2 The two transports (startup, lines 325–346)

`MCP_TRANSPORT` selects at startup (line 333):

| | `stdio` (default) | `streamable-http` |
|---|---|---|
| Who starts the server | The **client spawns the process** as a child | Runs standalone; here `uvicorn.run(mcp.streamable_http_app(), ...)` (lines 340–341) |
| Wire | JSON-RPC over the process's **stdin/stdout** | JSON-RPC over HTTP POST to `/mcp` |
| Deployment here (facts §5) | Mac: `~/.claude.json` spawns `venv/bin/python3.14 proton_email_server.py`; tools appear as `mcp__proton-mail__<tool>` | Desktop Docker container, `0.0.0.0:3004`; LibreChat points at `http://localhost:3004/mcp` |
| Auth | Inherits the spawning user's context | **None** — :3004 is unauthenticated and LAN-reachable (known-weak, facts §8; hardening-campaign target) |

**The stdout purity invariant.** Under stdio, stdout **is the wire**: every byte on stdout must be a JSON-RPC frame. A single stray `print()` corrupts the protocol and the client drops or errors the session. That is why lines 17–22 configure logging with `stream=sys.stderr`, and why all diagnostics go through `logger` — stderr is free for humans, stdout belongs to the protocol. Rule for any change to this file: **never print to stdout.**

`streamable_http_app()` (line 340) returns an ASGI application (the async Python web-server interface), which uvicorn serves — that's the whole reason `uvicorn` is a real dependency while `httpx`/`python-dotenv`/`secure-smtplib` in requirements.txt are vestigial (facts §2).

---

## When NOT to use this skill

This is the *theory* pack. For anything operational, use the sibling skills:

| You need to... | Use instead |
|---|---|
| Know the load-bearing design decisions, invariants, accepted weak points | `proton-mcp-architecture-contract` |
| Classify/gate/review a change (e.g. fix the SINCE bug, loosen the sanitizer, remove vestigial deps) | `proton-mcp-change-control` |
| Triage a live symptom (hangs, auth failures, empty results) | `proton-mcp-debugging-playbook` |
| Recreate the venv or Docker image; env-var traps | `proton-mcp-build-and-env` |
| Start/deploy either transport; full config table; redeploy to the desktop | `proton-mcp-run-and-operate` |
| Run connectivity checks, the read-only smoke script, source-drift checks | `proton-mcp-diagnostics-and-tooling` |
| Write or run tests; live-verification protocol | `proton-mcp-validation-and-qa` |
| Add or modify an MCP tool (incl. UID migration for delete/move features) | `proton-mcp-extending-tools` |
| Work the drift-elimination / transport-hardening campaign | `proton-mcp-drift-and-hardening-campaign` |

Also skip this skill for generic IMAP/SMTP questions unrelated to this repo — it deliberately covers only what this codebase touches.

## Provenance and maintenance

- **Sources:** `proton_email_server.py` read in full (346 lines, commit `d34a476`, the repo's only commit) and the verified facts pack dated **2026-07-02**. Deployment facts (Bridge container, ports, desktop :3004, LibreChat config) were verified 2026-07-02 by the coordinating engineer — treated as ground truth here, not re-verified by this skill's author.
- **General protocol claims** (IMAP atoms/literals/quoted strings, sequence-number vs UID semantics, RFC 2047 header encoding, MIME transfer encodings, STARTTLS vs SMTPS, `smtplib.send_message` Bcc stripping) are standard, uncontroversial IMAP/SMTP/MIME/Python-stdlib behavior stated from training, cross-checked against the code's observable structure.
- **UNVERIFIED items** (labeled inline): whether this Bridge build accepts STARTTLS on IMAP port 1143 (§2.1). Any Bridge-version-specific behavior beyond "self-signed cert, app password, ports 1143/1025 as deployed" should be treated as unconfirmed until tested against the running container.
- **Volatile facts** (all as of 2026-07-02): mcp package version 1.28.0 (the `@mcp.tool()` returns-original-function behavior is version-observed); the open `SINCE`-today/"7 days" bug at line 307; single-commit history; the unauthenticated :3004 posture.
- **Maintenance triggers:** re-check line references after any edit to `proton_email_server.py`; update §2.2 if the stats bug is fixed; update §5.1 if the mcp package is upgraded (re-verify decorator behavior); update §1/§4 if Bridge is reconfigured for TLS-verified operation; update the sibling table if skills are renamed.
- Never add credential values or the Proton account email address to this file.
