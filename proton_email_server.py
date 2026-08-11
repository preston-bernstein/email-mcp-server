#!/usr/bin/env python3
"""
Simple Proton Bridge Email MCP Server - Read and send emails via Proton Bridge
"""
import asyncio
import contextlib
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
    """Extract email body from message."""
    body = ""
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            if content_type in ["text/plain", "text/html"] and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset()
                if payload:
                    body += payload.decode(charset or "utf-8", errors="replace")
    else:
        payload = message.get_payload(decode=True)
        charset = message.get_content_charset()
        if payload:
            body = payload.decode(charset or "utf-8", errors="replace")
    return body


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
        except Exception:
            pass


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
    """Read recent emails from specified folder with optional count limit."""
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
    """Search emails in specified folder using IMAP search criteria."""
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

        # Proton Bridge issues a self-signed cert for its local SMTP listener,
        # so certificate verification is disabled here — safe only because
        # this is guarded to loopback connections.
        assert PROTON_BRIDGE_HOST in ("127.0.0.1", "localhost", "::1"), \
            "Refusing to disable TLS verification against a non-loopback SMTP host"
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
    """Send an email via Proton Bridge SMTP. Optionally send as an alias address you've added to your Proton account (from_email)."""
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
    """List all available email folders in the mailbox."""
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
    """Get statistics about emails in the specified folder."""
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
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        sys.exit(1)
