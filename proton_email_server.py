#!/usr/bin/env python3
"""
Simple Proton Bridge Email MCP Server - Read and send emails via Proton Bridge
"""
import asyncio
import contextlib
import ipaddress
import os
import re
import ssl
import sys
import logging
import imaplib
import smtplib
import email
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from mcp.server.fastmcp import FastMCP

# Configure logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger("proton-email-server")

mcp = FastMCP("proton-email")

# Configuration
PROTON_USERNAME = os.environ.get("PROTON_USERNAME", "")
PROTON_PASSWORD = os.environ.get("PROTON_PASSWORD", "")
PROTON_BRIDGE_HOST = os.environ.get("PROTON_BRIDGE_HOST", "127.0.0.1")
PROTON_BRIDGE_IMAP_PORT = int(os.environ.get("PROTON_BRIDGE_IMAP_PORT", "1143"))
PROTON_BRIDGE_SMTP_PORT = int(os.environ.get("PROTON_BRIDGE_SMTP_PORT", "1025"))

SOCKET_TIMEOUT_SECONDS = 15
MAX_RESULTS_CAP = 50

# === UTILITY FUNCTIONS ===

def decode_email_header(header):
    """Decode email header safely."""
    if not header:
        return "Unknown"
    decoded_parts = decode_header(header)
    decoded_string = ""
    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            decoded_string += part.decode(encoding if encoding else "utf-8", errors="replace")
        else:
            decoded_string += part
    return decoded_string

def extract_email_body(message):
    """Extract email body from message, preferring the plain-text part.

    Multipart/alternative messages carry the same content twice (once as
    text/plain, once as text/html); falling back to HTML markup only when
    no plain-text part exists avoids returning both concatenated together.
    """
    if not message.is_multipart():
        payload = message.get_payload(decode=True)
        charset = message.get_content_charset()
        return payload.decode(charset or "utf-8", errors="replace") if payload else ""

    html_body = ""
    for part in message.walk():
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition"))
        if "attachment" in content_disposition:
            continue

        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset()
        decoded = payload.decode(charset or "utf-8", errors="replace")

        if content_type == "text/plain":
            return decoded
        if content_type == "text/html" and not html_body:
            html_body = decoded

    return html_body


def _missing_credentials() -> str | None:
    """Return a user-facing error if Proton credentials aren't configured, else None."""
    if not PROTON_USERNAME or not PROTON_PASSWORD:
        return "❌ Error: Proton credentials not configured. Set PROTON_USERNAME and PROTON_PASSWORD."
    return None


def _parse_limit(raw: str, default: int, cap: int = MAX_RESULTS_CAP) -> int:
    """Parse a user-supplied count/max_results string, capped at `cap`."""
    limit = int(raw) if raw.strip() else default
    return min(limit, cap)


def _most_recent(ids, limit):
    """Return up to `limit` ids, most-recent first (IMAP returns ids oldest-first)."""
    return list(reversed(ids[-limit:]))


def _split_addrs(field: str):
    """Split a comma-separated address field into a list, or [] if empty."""
    return [addr.strip() for addr in field.split(",")] if field.strip() else []


def _is_trusted_bridge_host(host: str) -> bool:
    """True for loopback or private-network hosts — where running an
    unverified-TLS connection to a self-signed Proton Bridge is acceptable."""
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def _format_message(message, index, label="Email", include_body=False, separator_width=60):
    """Render one fetched email as the text block a tool returns."""
    subject = decode_email_header(message["Subject"])
    sender = decode_email_header(message["From"])
    date = message.get("Date", "Unknown")

    block = f"{label} #{index}\n"
    block += f"  📤 From: {sender}\n"
    block += f"  📋 Subject: {subject}\n"
    block += f"  📅 Date: {date}\n"
    if include_body:
        body = extract_email_body(message)
        block += f"  📄 Body: {body[:300]}{'...' if len(body) > 300 else ''}\n"
    block += "-" * separator_width + "\n"
    return block


class FolderSelectError(Exception):
    """Raised when an IMAP folder can't be selected."""


@contextlib.contextmanager
def imap_session(folder: str | None = None):
    """Connect and log in to Proton Bridge IMAP, optionally select `folder`,
    and guarantee logout even if the caller raises."""
    mail = imaplib.IMAP4(PROTON_BRIDGE_HOST, PROTON_BRIDGE_IMAP_PORT)
    mail.sock.settimeout(SOCKET_TIMEOUT_SECONDS)
    try:
        mail.login(PROTON_USERNAME, PROTON_PASSWORD)
        if folder is not None:
            status, _ = mail.select(folder)
            if status != "OK":
                raise FolderSelectError(f"Could not select folder '{folder}'")
        yield mail
    finally:
        try:
            mail.logout()
        except Exception as exc:
            logger.debug(f"IMAP logout failed (ignored): {exc}")


def _log_failure(context: str, exc: Exception):
    """Log with a full traceback plus a coarse failure class, for operator diagnosis."""
    if isinstance(exc, (imaplib.IMAP4.error, smtplib.SMTPException)):
        kind = "protocol error"
    elif isinstance(exc, OSError):
        kind = "network error"
    else:
        kind = "unexpected error"
    logger.exception(f"{context} ({kind})")

# === MCP TOOLS ===

def _read_recent_emails_sync(count: str, folder: str) -> str:
    logger.info(f"Reading {count} emails from {folder}")

    if (msg := _missing_credentials()):
        return msg

    try:
        limit = _parse_limit(count, default=10)
    except ValueError:
        return f"❌ Error: Invalid count value: {count}"

    try:
        with imap_session(folder) as mail:
            status, messages = mail.search(None, "ALL")
            mail_ids = messages[0].split()

            if not mail_ids:
                return f"📭 No emails found in {folder}."

            recent_ids = _most_recent(mail_ids, limit)
            result = f"📧 Found {len(mail_ids)} total emails in {folder}. Showing {len(recent_ids)} most recent:\n\n"

            for i, mail_id in enumerate(recent_ids, 1):
                try:
                    status, msg_data = mail.fetch(mail_id, "(RFC822)")
                    raw_email = msg_data[0][1]
                    message = email.message_from_bytes(raw_email)
                    result += _format_message(message, i, label="Email", include_body=True)
                except Exception as e:
                    logger.warning(f"Skipping unparseable message {mail_id!r} in {folder}: {e}")
                    result += f"Email #{i}\n  ⚠️ Could not parse this message: {e}\n" + "-" * 60 + "\n"

            return result

    except FolderSelectError as e:
        return f"❌ Error: {e}"
    except Exception as e:
        _log_failure("Error reading emails", e)
        return f"❌ Error reading emails: {str(e)}"


@mcp.tool()
async def read_recent_emails(count: str = "10", folder: str = "INBOX") -> str:
    """Read the most recent emails from one IMAP folder and return them as a formatted text block (not JSON), newest first, each entry showing From, Subject, Date, and a body preview truncated to 300 characters. Returns a plain error string, not an exception, if Proton credentials are missing, the folder does not exist, or the connection times out; a message that cannot be parsed is reported inline as a warning instead of failing the whole call. `count` is capped at 50 regardless of what is requested, so it never returns more than that even for a folder with thousands of messages. Use search_emails instead when you need to filter by content rather than just take the newest N messages.

    Args:
        count: Number of most recent emails to return, as a numeric string (e.g. "10"). Defaults to "10". Silently capped at 50 even if a higher value is passed. Must parse as an integer; a non-numeric value returns an error string instead of raising.
        folder: IMAP folder name to read from (e.g. "INBOX", "Sent"). Defaults to "INBOX". If the folder does not exist or can't be selected, returns an error string instead of raising.
    """
    return await asyncio.to_thread(_read_recent_emails_sync, count, folder)


def _search_emails_sync(query: str, folder: str, max_results: str) -> str:
    logger.info(f"Searching emails with query: {query}")

    if not query.strip():
        return "❌ Error: Search query is required"

    if (msg := _missing_credentials()):
        return msg

    # Strip chars illegal in IMAP search literals before touching the network
    safe_query = re.sub(r'[^\w\s@.\-]', '', query).strip()
    if not safe_query:
        return f"❌ Error: Query '{query}' contains no searchable characters"

    try:
        limit = _parse_limit(max_results, default=20)
    except ValueError:
        return f"❌ Error: Invalid max_results value: {max_results}"

    try:
        with imap_session(folder) as mail:
            # TEXT searches subject + headers + body; fall back to SUBJECT-only if Bridge rejects TEXT
            try:
                status, messages = mail.search(None, "TEXT", safe_query)
                if status != "OK":
                    raise ValueError("TEXT search returned non-OK status")
            except Exception as e:
                logger.debug(f"TEXT search failed ({e}); falling back to SUBJECT search")
                status, messages = mail.search(None, "SUBJECT", safe_query)

            mail_ids = messages[0].split() if messages and messages[0] else []

            if not mail_ids:
                return f"🔍 No emails found matching '{query}' in {folder}"

            found_ids = _most_recent(mail_ids, limit)
            result = f"🔍 Found {len(mail_ids)} emails matching '{query}' in {folder}. Showing {len(found_ids)} most recent:\n\n"

            for i, mail_id in enumerate(found_ids, 1):
                try:
                    status, msg_data = mail.fetch(mail_id, "(RFC822)")
                    raw_email = msg_data[0][1]
                    message = email.message_from_bytes(raw_email)
                    result += _format_message(message, i, label="Result", separator_width=40)
                except Exception as e:
                    logger.warning(f"Skipping unparseable message {mail_id!r} in {folder}: {e}")
                    result += f"Result #{i}\n  ⚠️ Could not parse this message: {e}\n" + "-" * 40 + "\n"

            return result

    except FolderSelectError as e:
        return f"❌ Error: {e}"
    except Exception as e:
        _log_failure("Error searching emails", e)
        return f"❌ Error searching emails: {str(e)}"


@mcp.tool()
async def search_emails(query: str = "", folder: str = "INBOX", max_results: str = "20") -> str:
    """Search one IMAP folder for a text query and return matching emails as a formatted text block (not JSON), newest match first, each entry showing only From, Subject, and Date; the message body is not included in search results. The query is sanitized to letters, digits, spaces, @, period, and hyphen before searching, other characters are stripped, and a query with nothing left after stripping returns an error string. It first tries a full TEXT search across subject, headers, and body, and silently falls back to a SUBJECT-only search if the Bridge rejects the TEXT search, so a body-only match can be missed with no indication of which search mode actually ran. `max_results` is capped at 50 regardless of what is requested. Use read_recent_emails instead when you just want the newest messages with a full body preview, not a filtered search.

    Args:
        query: Search text to match against email subject, headers, and (when supported) body. Required; an empty or whitespace-only string returns an error. Characters other than letters, digits, spaces, @, period, and hyphen are stripped before the IMAP search runs.
        folder: IMAP folder to search (e.g. "INBOX"). Defaults to "INBOX".
        max_results: Maximum number of matches to return, as a numeric string. Defaults to "20". Silently capped at 50.
    """
    return await asyncio.to_thread(_search_emails_sync, query, folder, max_results)


def _send_email_sync(to_email: str, subject: str, body: str, cc: str, bcc: str, from_email: str) -> str:
    logger.info(f"Sending email to {to_email}")

    if not to_email.strip() or not subject.strip() or not body.strip():
        return "❌ Error: to_email, subject, and body are required"

    if (msg := _missing_credentials()):
        return msg

    try:
        mime_msg = MIMEMultipart()
        mime_msg['From'] = from_email.strip() if from_email.strip() else PROTON_USERNAME
        mime_msg['To'] = to_email.strip()
        mime_msg['Subject'] = subject.strip()

        if cc.strip():
            mime_msg['Cc'] = cc.strip()
        # Bcc is intentionally never set as a message header — a Bcc header
        # would be visible to every recipient, defeating the point of a
        # blind copy. Bcc recipients still receive the message via to_addrs.

        mime_msg.attach(MIMEText(body.strip(), 'plain'))

        recipients = [to_email.strip()] + _split_addrs(cc) + _split_addrs(bcc)

        # Proton Bridge issues a self-signed cert per install, and this server
        # is sometimes pointed at a Bridge running on another machine on the
        # trusted home LAN (not just loopback) — so certificate verification
        # is disabled here, guarded to loopback/private-network hosts only.
        assert _is_trusted_bridge_host(PROTON_BRIDGE_HOST), \
            "Refusing to disable TLS verification against an untrusted (non-private) SMTP host"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with smtplib.SMTP(PROTON_BRIDGE_HOST, PROTON_BRIDGE_SMTP_PORT) as server:
            server.starttls(context=ctx)
            server.login(PROTON_USERNAME, PROTON_PASSWORD)
            server.send_message(mime_msg, to_addrs=recipients)

        return f"✅ Email sent successfully to {to_email}"

    except Exception as e:
        _log_failure("Error sending email", e)
        return f"❌ Error sending email: {str(e)}"


@mcp.tool()
async def send_email(to_email: str = "", subject: str = "", body: str = "", cc: str = "", bcc: str = "", from_email: str = "") -> str:
    """Send a single email through Proton Bridge's SMTP relay and return a plain text status string, not a structured result: either a success message or a "Error: ..." message describing what went wrong. to_email, subject, and body are required; an empty or whitespace-only value in any of them returns an error without attempting to send. bcc recipients receive the message through the SMTP envelope but are never added as a visible header, so other recipients cannot see them; cc recipients are visible to everyone. Optionally set from_email to send as a verified alias on the Proton account instead of the default account address. TLS certificate verification is disabled for the connection to Bridge, which is safe only because this server refuses to run against any SMTP host outside loopback or a private network; it does not attach files or support HTML bodies.

    Args:
        to_email: Primary recipient. Required; empty/whitespace-only errors instead of sending.
        subject: Email subject. Required; empty/whitespace-only errors instead of sending.
        body: Plain-text body. Required; empty/whitespace-only errors instead of sending. text/plain only, no HTML or attachments.
        cc: Comma-separated CC addresses. Optional, default none. Visible to every recipient.
        bcc: Comma-separated BCC addresses. Optional, default none. Never added as a visible header.
        from_email: Alias address to send as. Optional; defaults to the account's own address.
    """
    return await asyncio.to_thread(_send_email_sync, to_email, subject, body, cc, bcc, from_email)


def _list_folders_sync() -> str:
    logger.info("Listing email folders")

    if (msg := _missing_credentials()):
        return msg

    try:
        with imap_session() as mail:
            status, folders = mail.list()

            if status != 'OK':
                return "❌ Error: Failed to retrieve folder list"

            result = "📁 Available email folders:\n\n"
            for folder in folders:
                folder_str = folder.decode('utf-8') if isinstance(folder, bytes) else str(folder)
                # Parse folder name from IMAP response
                parts = folder_str.split('"')
                if len(parts) >= 3:
                    folder_name = parts[-2]
                    result += f"  📂 {folder_name}\n"
                else:
                    logger.warning(f"Could not parse folder line: {folder_str!r}")

            return result

    except Exception as e:
        _log_failure("Error listing folders", e)
        return f"❌ Error listing folders: {str(e)}"


@mcp.tool()
async def list_folders() -> str:
    """List every IMAP folder name in the mailbox as a plain text list (not JSON), one folder per line. It takes no parameters and always returns the full folder list; it cannot filter, search within a folder, or return folder-level stats like message counts (use get_email_stats for that, one folder at a time). A folder line the IMAP response can't parse is silently dropped from the output rather than shown as an error, so the returned count may undercount the true number of folders. Returns a plain error string, not an exception, if Proton credentials are missing or the connection fails."""
    return await asyncio.to_thread(_list_folders_sync)


def _get_email_stats_sync(folder: str) -> str:
    logger.info(f"Getting stats for folder: {folder}")

    if (msg := _missing_credentials()):
        return msg

    try:
        with imap_session(folder) as mail:
            # Total count
            status, total_msgs = mail.search(None, "ALL")
            total_count = len(total_msgs[0].split()) if total_msgs[0] else 0

            # Unread count
            status, unread_msgs = mail.search(None, "UNSEEN")
            unread_count = len(unread_msgs[0].split()) if unread_msgs[0] else 0

            # Recent count (last 7 days)
            since_date = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
            status, recent_msgs = mail.search(None, "SINCE", since_date)
            recent_count = len(recent_msgs[0].split()) if recent_msgs[0] else 0

            result = f"📊 Email Statistics for {folder}:\n\n"
            result += f"  📧 Total emails: {total_count}\n"
            result += f"  📬 Unread emails: {unread_count}\n"
            result += f"  🆕 Recent emails (7 days): {recent_count}\n"
            result += f"  📖 Read emails: {total_count - unread_count}\n"

            return result

    except FolderSelectError as e:
        return f"❌ Error: {e}"
    except Exception as e:
        _log_failure("Error getting email stats", e)
        return f"❌ Error getting email stats: {str(e)}"


@mcp.tool()
async def get_email_stats(folder: str = "INBOX") -> str:
    """Return message counts for one IMAP folder as a plain text block (not JSON): total emails, unread emails, emails from the last 7 days, and read emails (total minus unread). The 7-day window for "recent" is fixed and cannot be changed by a parameter. It only covers the one folder passed in; call it once per folder to compare folders, since there is no way to get stats across the whole mailbox in one call. Returns a plain error string, not an exception, if Proton credentials are missing, the folder doesn't exist, or the connection fails.

    Args:
        folder: IMAP folder to compute statistics for (e.g. "INBOX"). Defaults to "INBOX". If the folder doesn't exist or can't be selected, returns an error string instead of raising.
    """
    return await asyncio.to_thread(_get_email_stats_sync, folder)

# === SERVER STARTUP ===
if __name__ == "__main__":
    logger.info("Starting Proton Bridge Email MCP server...")

    if not PROTON_USERNAME:
        logger.warning("PROTON_USERNAME not set")
    if not PROTON_PASSWORD:
        logger.warning("PROTON_PASSWORD not set")

    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    try:
        if transport == "streamable-http":
            import uvicorn
            host = os.environ.get("MCP_HOST", "0.0.0.0")
            port = int(os.environ.get("MCP_PORT", "3004"))
            app = mcp.streamable_http_app()
            uvicorn.run(app, host=host, port=port)
        else:
            mcp.run(transport="stdio")
    except Exception:
        logger.exception("Server error")
        sys.exit(1)
