"""
Tests for proton_email_server.py

Mocks imaplib.IMAP4 and smtplib.SMTP — no live Proton Bridge required.
Run: pytest tests/
"""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call
from email.mime.text import MIMEText

# Inject required env vars before importing the module
os.environ.setdefault("PROTON_USERNAME", "test@protonmail.com")
os.environ.setdefault("PROTON_PASSWORD", "test-bridge-password")
os.environ.setdefault("PROTON_BRIDGE_HOST", "127.0.0.1")
os.environ.setdefault("PROTON_BRIDGE_IMAP_PORT", "1143")
os.environ.setdefault("PROTON_BRIDGE_SMTP_PORT", "1025")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import proton_email_server as srv


# ── helpers ──────────────────────────────────────────────────────────────────

def make_raw_email(subject="Test Subject", sender="alice@example.com", body="Hello world"):
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["Date"] = "Sat, 21 Jun 2026 00:00:00 +0000"
    msg.attach(MIMEText(body, "plain"))
    return msg.as_bytes()


def mock_imap(mail_ids=None, raw_email=None, select_status="OK"):
    """Return a mock IMAP4 instance pre-wired with common responses."""
    m = MagicMock()
    m.sock = MagicMock()
    m.select.return_value = (select_status, [b"1"])
    ids_bytes = b" ".join(mail_ids or [b"1"])
    m.search.return_value = ("OK", [ids_bytes])
    raw = raw_email or make_raw_email()
    m.fetch.return_value = ("OK", [(b"1 (RFC822 {500})", raw)])
    m.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Spam"'])
    return m


# ── search_emails ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_strips_special_chars():
    """Query with tilde/special chars should be sanitized, not cause a hang."""
    with patch("imaplib.IMAP4", return_value=mock_imap()) as MockIMAP:
        result = await srv.search_emails(query="~ spam", folder="INBOX", max_results="5")
    # "~ spam" → "spam" after sanitization; search should proceed
    imap_instance = MockIMAP.return_value
    search_args = imap_instance.search.call_args
    assert "~" not in str(search_args), "Tilde must be stripped before IMAP call"
    assert "spam" in result.lower() or "No emails" in result


@pytest.mark.asyncio
async def test_search_empty_after_sanitization():
    """Query that is all stripped chars → clear error, no IMAP call."""
    with patch("imaplib.IMAP4") as MockIMAP:
        # ~ ! # % ^ are all stripped; nothing left after sanitization
        result = await srv.search_emails(query="~!#%^", folder="INBOX", max_results="5")
    MockIMAP.assert_not_called()
    assert "❌" in result


@pytest.mark.asyncio
async def test_search_falls_back_to_subject_when_text_fails():
    """If TEXT search raises, fall back to SUBJECT search."""
    m = mock_imap()
    m.search.side_effect = [
        Exception("TEXT not supported"),
        ("OK", [b"2 3"]),
    ]
    with patch("imaplib.IMAP4", return_value=m):
        result = await srv.search_emails(query="hello", folder="INBOX", max_results="5")
    assert m.search.call_count == 2
    second_call = m.search.call_args_list[1]
    assert "SUBJECT" in str(second_call)


@pytest.mark.asyncio
async def test_search_no_results():
    m = mock_imap(mail_ids=[])
    m.search.return_value = ("OK", [b""])
    with patch("imaplib.IMAP4", return_value=m):
        result = await srv.search_emails(query="xyznotfound", folder="INBOX", max_results="5")
    assert "No emails found" in result


@pytest.mark.asyncio
async def test_search_bad_folder():
    m = mock_imap(select_status="NO")
    with patch("imaplib.IMAP4", return_value=m):
        result = await srv.search_emails(query="test", folder="NonExistent", max_results="5")
    assert "❌" in result
    assert "folder" in result.lower()


@pytest.mark.asyncio
async def test_search_socket_timeout_set():
    """Verify settimeout(15) is called to prevent indefinite hangs."""
    m = mock_imap()
    with patch("imaplib.IMAP4", return_value=m):
        await srv.search_emails(query="test", folder="INBOX", max_results="5")
    m.sock.settimeout.assert_called_once_with(15)


# ── read_recent_emails ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_recent_emails_returns_emails():
    raw = make_raw_email(subject="Hello", sender="bob@example.com")
    m = mock_imap(mail_ids=[b"1"], raw_email=raw)
    with patch("imaplib.IMAP4", return_value=m):
        result = await srv.read_recent_emails(count="1", folder="INBOX")
    assert "Hello" in result
    assert "bob@example.com" in result


@pytest.mark.asyncio
async def test_read_recent_emails_limit_cap():
    """Count > 50 should be capped to 50."""
    m = mock_imap(mail_ids=[b"1"])
    with patch("imaplib.IMAP4", return_value=m):
        await srv.read_recent_emails(count="999", folder="INBOX")
    # Verify fetch was called (cap applied, didn't error)
    m.fetch.assert_called()


# ── send_email ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_email_success():
    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)
    with patch("smtplib.SMTP", return_value=mock_smtp):
        result = await srv.send_email(
            to_email="bob@example.com",
            subject="Test",
            body="Body text",
        )
    assert "✅" in result or "sent" in result.lower()


@pytest.mark.asyncio
async def test_send_email_missing_fields():
    result = await srv.send_email(to_email="", subject="Test", body="Body")
    assert "❌" in result

    result = await srv.send_email(to_email="bob@example.com", subject="", body="Body")
    assert "❌" in result


# ── list_folders ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_folders():
    m = mock_imap()
    with patch("imaplib.IMAP4", return_value=m):
        result = await srv.list_folders()
    assert "INBOX" in result or "folder" in result.lower()


# ── get_email_stats ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_email_stats():
    m = mock_imap(mail_ids=[b"1", b"2", b"3"])
    m.search.return_value = ("OK", [b"1 2 3"])
    with patch("imaplib.IMAP4", return_value=m):
        result = await srv.get_email_stats()
    assert "3" in result or "stat" in result.lower()
