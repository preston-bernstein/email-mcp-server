#!/usr/bin/env python3
"""
Simple Proton Bridge Email MCP Server - Read and send emails via Proton Bridge
"""
import os
import sys
import logging
import imaplib
import smtplib
import email
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP

# Configure logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger("proton-email-server")

# Initialize MCP server - NO PROMPT PARAMETER!
mcp = FastMCP("proton-email")

# Configuration
PROTON_USERNAME = os.environ.get("PROTON_USERNAME", "")
PROTON_PASSWORD = os.environ.get("PROTON_PASSWORD", "")
PROTON_BRIDGE_HOST = os.environ.get("PROTON_BRIDGE_HOST", "127.0.0.1")
PROTON_BRIDGE_IMAP_PORT = int(os.environ.get("PROTON_BRIDGE_IMAP_PORT", "1143"))
PROTON_BRIDGE_SMTP_PORT = int(os.environ.get("PROTON_BRIDGE_SMTP_PORT", "1025"))

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

# === MCP TOOLS ===

@mcp.tool()
async def read_recent_emails(count: str = "10", folder: str = "INBOX") -> str:
    """Read recent emails from specified folder with optional count limit."""
    logger.info(f"Reading {count} emails from {folder}")
    
    if not PROTON_USERNAME or not PROTON_PASSWORD:
        return "❌ Error: Proton credentials not configured. Set PROTON_USERNAME and PROTON_PASSWORD."
    
    try:
        limit = int(count) if count.strip() else 10
        if limit > 50:
            limit = 50  # Safety limit
            
        mail = imaplib.IMAP4(PROTON_BRIDGE_HOST, PROTON_BRIDGE_IMAP_PORT)
        mail.login(PROTON_USERNAME, PROTON_PASSWORD)
        mail.select(folder)
        
        status, messages = mail.search(None, "ALL")
        mail_ids = messages[0].split()
        
        if not mail_ids:
            mail.logout()
            return f"📭 No emails found in {folder}."
        
        # Get most recent emails
        recent_ids = mail_ids[-limit:] if len(mail_ids) >= limit else mail_ids
        recent_ids.reverse()  # Most recent first
        
        result = f"📧 Found {len(mail_ids)} total emails in {folder}. Showing {len(recent_ids)} most recent:\n\n"
        
        for i, mail_id in enumerate(recent_ids, 1):
            status, msg_data = mail.fetch(mail_id, "(RFC822)")
            raw_email = msg_data[0][1]
            message = email.message_from_bytes(raw_email)
            
            subject = decode_email_header(message["Subject"])
            sender = decode_email_header(message["From"])
            date = message.get("Date", "Unknown")
            body = extract_email_body(message)
            
            result += f"Email #{i}\n"
            result += f"  📤 From: {sender}\n"
            result += f"  📋 Subject: {subject}\n"
            result += f"  📅 Date: {date}\n"
            result += f"  📄 Body: {body[:300]}{'...' if len(body) > 300 else ''}\n"
            result += "-" * 60 + "\n"
        
        mail.logout()
        return result
        
    except ValueError:
        return f"❌ Error: Invalid count value: {count}"
    except Exception as e:
        logger.error(f"Error reading emails: {e}")
        return f"❌ Error reading emails: {str(e)}"

@mcp.tool()
async def search_emails(query: str = "", folder: str = "INBOX", max_results: str = "20") -> str:
    """Search emails in specified folder using IMAP search criteria."""
    logger.info(f"Searching emails with query: {query}")
    
    if not query.strip():
        return "❌ Error: Search query is required"
    
    if not PROTON_USERNAME or not PROTON_PASSWORD:
        return "❌ Error: Proton credentials not configured"
    
    # Strip chars illegal in IMAP search literals before touching the network
    import re
    safe_query = re.sub(r'[^\w\s@.\-]', '', query).strip()
    if not safe_query:
        return f"❌ Error: Query '{query}' contains no searchable characters"

    try:
        limit = int(max_results) if max_results.strip() else 20
        if limit > 50:
            limit = 50

        mail = imaplib.IMAP4(PROTON_BRIDGE_HOST, PROTON_BRIDGE_IMAP_PORT)
        mail.sock.settimeout(15)  # prevent indefinite hang on malformed queries
        mail.login(PROTON_USERNAME, PROTON_PASSWORD)
        status, _ = mail.select(folder)
        if status != "OK":
            mail.logout()
            return f"❌ Error: Could not select folder '{folder}'"

        # TEXT searches subject + headers + body; fall back to SUBJECT-only if Bridge rejects TEXT
        try:
            status, messages = mail.search(None, "TEXT", safe_query)
            if status != "OK":
                raise ValueError("TEXT search returned non-OK status")
        except Exception:
            status, messages = mail.search(None, "SUBJECT", safe_query)

        mail_ids = messages[0].split() if messages and messages[0] else []

        if not mail_ids:
            mail.logout()
            return f"🔍 No emails found matching '{query}' in {folder}"
        
        # Limit results
        found_ids = mail_ids[-limit:] if len(mail_ids) >= limit else mail_ids
        found_ids.reverse()
        
        result = f"🔍 Found {len(mail_ids)} emails matching '{query}' in {folder}. Showing {len(found_ids)} most recent:\n\n"
        
        for i, mail_id in enumerate(found_ids, 1):
            status, msg_data = mail.fetch(mail_id, "(RFC822)")
            raw_email = msg_data[0][1]
            message = email.message_from_bytes(raw_email)
            
            subject = decode_email_header(message["Subject"])
            sender = decode_email_header(message["From"])
            date = message.get("Date", "Unknown")
            
            result += f"Result #{i}\n"
            result += f"  📤 From: {sender}\n"
            result += f"  📋 Subject: {subject}\n"
            result += f"  📅 Date: {date}\n"
            result += "-" * 40 + "\n"
        
        mail.logout()
        return result
        
    except ValueError:
        return f"❌ Error: Invalid max_results value: {max_results}"
    except Exception as e:
        logger.error(f"Error searching emails: {e}")
        return f"❌ Error searching emails: {str(e)}"

@mcp.tool()
async def send_email(to_email: str = "", subject: str = "", body: str = "", cc: str = "", bcc: str = "") -> str:
    """Send an email via Proton Bridge SMTP."""
    logger.info(f"Sending email to {to_email}")
    
    if not to_email.strip() or not subject.strip() or not body.strip():
        return "❌ Error: to_email, subject, and body are required"
    
    if not PROTON_USERNAME or not PROTON_PASSWORD:
        return "❌ Error: Proton credentials not configured"
    
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = PROTON_USERNAME
        msg['To'] = to_email.strip()
        msg['Subject'] = subject.strip()
        
        if cc.strip():
            msg['Cc'] = cc.strip()
        if bcc.strip():
            msg['Bcc'] = bcc.strip()
        
        # Attach body
        msg.attach(MIMEText(body.strip(), 'plain'))
        
        # Send via SMTP
        import ssl as _ssl
        _ctx = _ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = _ssl.CERT_NONE
        with smtplib.SMTP(PROTON_BRIDGE_HOST, PROTON_BRIDGE_SMTP_PORT) as server:
            server.starttls(context=_ctx)
            server.login(PROTON_USERNAME, PROTON_PASSWORD)
            
            # Prepare recipient list
            recipients = [to_email.strip()]
            if cc.strip():
                recipients.extend([addr.strip() for addr in cc.split(',')])
            if bcc.strip():
                recipients.extend([addr.strip() for addr in bcc.split(',')])
            
            server.send_message(msg, to_addrs=recipients)
        
        return f"✅ Email sent successfully to {to_email}"
        
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return f"❌ Error sending email: {str(e)}"

@mcp.tool()
async def list_folders() -> str:
    """List all available email folders in the mailbox."""
    logger.info("Listing email folders")
    
    if not PROTON_USERNAME or not PROTON_PASSWORD:
        return "❌ Error: Proton credentials not configured"
    
    try:
        mail = imaplib.IMAP4(PROTON_BRIDGE_HOST, PROTON_BRIDGE_IMAP_PORT)
        mail.login(PROTON_USERNAME, PROTON_PASSWORD)
        
        status, folders = mail.list()
        mail.logout()
        
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
        
        return result
        
    except Exception as e:
        logger.error(f"Error listing folders: {e}")
        return f"❌ Error listing folders: {str(e)}"

@mcp.tool()
async def get_email_stats(folder: str = "INBOX") -> str:
    """Get statistics about emails in the specified folder."""
    logger.info(f"Getting stats for folder: {folder}")
    
    if not PROTON_USERNAME or not PROTON_PASSWORD:
        return "❌ Error: Proton credentials not configured"
    
    try:
        mail = imaplib.IMAP4(PROTON_BRIDGE_HOST, PROTON_BRIDGE_IMAP_PORT)
        mail.login(PROTON_USERNAME, PROTON_PASSWORD)
        mail.select(folder)
        
        # Get total count
        status, total_msgs = mail.search(None, "ALL")
        total_count = len(total_msgs[0].split()) if total_msgs[0] else 0
        
        # Get unread count
        status, unread_msgs = mail.search(None, "UNSEEN")
        unread_count = len(unread_msgs[0].split()) if unread_msgs[0] else 0
        
        # Get recent count (from last 7 days)
        status, recent_msgs = mail.search(None, "SINCE", datetime.now().strftime("%d-%b-%Y"))
        recent_count = len(recent_msgs[0].split()) if recent_msgs[0] else 0
        
        mail.logout()
        
        result = f"📊 Email Statistics for {folder}:\n\n"
        result += f"  📧 Total emails: {total_count}\n"
        result += f"  📬 Unread emails: {unread_count}\n"
        result += f"  🆕 Recent emails (7 days): {recent_count}\n"
        result += f"  📖 Read emails: {total_count - unread_count}\n"
        
        return result
        
    except Exception as e:
        logger.error(f"Error getting email stats: {e}")
        return f"❌ Error getting email stats: {str(e)}"

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
            from mcp.server.fastmcp import FastMCP as _FastMCP
            host = os.environ.get("MCP_HOST", "0.0.0.0")
            port = int(os.environ.get("MCP_PORT", "3004"))
            app = mcp.streamable_http_app()
            uvicorn.run(app, host=host, port=port)
        else:
            mcp.run(transport="stdio")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        sys.exit(1)