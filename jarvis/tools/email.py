"""Gmail integration via OAuth: search, read, and send mail.

Credentials come from a one-time interactive authorisation
(``scripts/gmail_auth.py``), never raw SMTP/IMAP or an app password. The saved
token lives at ``config.GMAIL_TOKEN_PATH`` and every tool here refreshes it
automatically if it has expired. Nothing in this module can complete the
initial consent flow itself -- that needs a real browser and a human clicking
through Google's screen -- so a missing or dead token comes back as a clean
error pointing at the setup script rather than a stack trace.

Scopes are the minimum needed for what these three tools actually do:
``gmail.readonly`` for search/read, ``gmail.send`` for send -- not the broad
``gmail.modify`` or full-mailbox scopes.
"""

from __future__ import annotations

import base64
import re
from email.mime.text import MIMEText
from typing import Any

from ..config import GMAIL_TOKEN_PATH, google_client_id, google_client_secret
from ..log import get
from ..security import Risk
from .registry import tool

log = get("jarvis.tools.email")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


# --------------------------------------------------------------------------
# Shared auth helper
# --------------------------------------------------------------------------


def _load_credentials():
    """Return (Credentials, None) or (None, actionable error message)."""
    if not GMAIL_TOKEN_PATH.exists():
        return None, (
            "Gmail is not authorized yet. Run scripts/gmail_auth.py to "
            "authorize Gmail access first."
        )

    try:
        google_client_id()
        google_client_secret()
    except RuntimeError as exc:
        return None, str(exc)

    from google.oauth2.credentials import Credentials

    try:
        creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_PATH), SCOPES)
    except Exception as exc:
        return None, (
            f"The saved Gmail token at {GMAIL_TOKEN_PATH} could not be read "
            f"({exc}). Run scripts/gmail_auth.py to re-authorize."
        )

    if creds.valid:
        return creds, None

    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request as GoogleAuthRequest

        try:
            creds.refresh(GoogleAuthRequest())
        except Exception as exc:
            return None, (
                f"The saved Gmail token could not be refreshed ({exc}). Run "
                f"scripts/gmail_auth.py to re-authorize."
            )
        # Persist the refreshed access token so the next call doesn't have to
        # refresh again.
        GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        return creds, None

    return None, (
        "The saved Gmail token is invalid and has no refresh token. Run "
        "scripts/gmail_auth.py to re-authorize."
    )


def _gmail_service():
    """Return (service, None) or (None, actionable error message)."""
    creds, error = _load_credentials()
    if error:
        return None, error

    from googleapiclient.discovery import build

    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as exc:
        return None, f"Could not start the Gmail client: {exc}"
    return service, None


# --------------------------------------------------------------------------
# Body decoding
# --------------------------------------------------------------------------


def _decode_part(data: str) -> str:
    """Gmail's message parts are base64url, not standard base64."""
    padded = data + "=" * (-len(data) % 4)
    raw = base64.urlsafe_b64decode(padded)
    return raw.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    # Same crude, dependency-free approach as web.fetch_url: drop non-content
    # elements, strip tags, collapse whitespace.
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&(nbsp|amp|lt|gt|quot|#39);", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_body(payload: dict[str, Any]) -> str:
    """Walk a Gmail message payload for the best available body text.

    Prefers the first text/plain part found; falls back to stripping tags out
    of text/html if that is all the message offers.
    """
    plain: str | None = None
    html: str | None = None

    def walk(part: dict[str, Any]) -> None:
        nonlocal plain, html
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and mime == "text/plain" and plain is None:
            plain = _decode_part(data)
        elif data and mime == "text/html" and html is None:
            html = _decode_part(data)
        for sub_part in part.get("parts") or []:
            walk(sub_part)

    walk(payload)
    if plain:
        return plain.strip()
    if html:
        return _strip_html(html)
    return ""


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {h["name"]: h["value"] for h in payload.get("headers", []) if "name" in h}


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@tool(
    risk=Risk.MODERATE,
    params={
        "query": "Gmail search query, using Gmail's own search syntax, e.g. "
                 "'from:alex@example.com is:unread', 'subject:invoice after:2026/08/01', "
                 "'has:attachment label:work'.",
        "max_results": "Maximum number of matching messages to return (1-25).",
    },
    summary=lambda a: f"Search Gmail for {a.get('query', '?')!r}",
    tags=["email"],
)
def search_emails(query: str, max_results: int = 10) -> dict:
    """Search the user's Gmail inbox and list matching messages.

    Returns each match's id, from, subject, date, and a short snippet -- not
    the full body, so this is cheap to call broadly. Pass a match's id to
    read_email if you need the complete message.
    """
    service, error = _gmail_service()
    if error:
        return {"ok": False, "error": error}

    count = max(1, min(int(max_results), 25))
    try:
        listing = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=count)
            .execute()
        )
    except Exception as exc:
        return {"ok": False, "error": f"Gmail search failed: {exc}"}

    message_ids = [m["id"] for m in listing.get("messages", [])]
    if not message_ids:
        return {"query": query, "count": 0, "results": []}

    results = []
    for message_id in message_ids:
        try:
            msg = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
        except Exception as exc:
            log.warning("could not fetch message %s: %s", message_id, exc)
            continue
        headers = _headers(msg.get("payload", {}))
        results.append(
            {
                "id": message_id,
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
            }
        )

    return {"query": query, "count": len(results), "results": results}


@tool(
    risk=Risk.MODERATE,
    params={"message_id": "The Gmail message id, as returned by search_emails."},
    summary=lambda a: f"Read email {a.get('message_id', '?')}",
    tags=["email"],
)
def read_email(message_id: str) -> dict:
    """Fetch one email in full: from, to, subject, date, and plain-text body.

    Get message_id from search_emails first. Prefers the message's own
    text/plain part; if it only has HTML, tags are stripped so you still get
    readable text.
    """
    service, error = _gmail_service()
    if error:
        return {"ok": False, "error": error}

    try:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
    except Exception as exc:
        return {"ok": False, "error": f"Could not read message {message_id}: {exc}"}

    payload = msg.get("payload", {})
    headers = _headers(payload)
    body = _extract_body(payload)

    return {
        "id": message_id,
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "body": body,
    }


@tool(
    risk=Risk.HIGH,
    params={
        "to": "Recipient email address.",
        "subject": "Email subject line.",
        "body": "Plain-text email body.",
        "cc": "Optional comma-separated CC address(es).",
    },
    summary=lambda a: f"Send email to {a.get('to', '?')}: {(a.get('subject') or '')[:60]!r}",
    tags=["email"],
)
def send_email(to: str, subject: str, body: str, cc: str | None = None) -> dict:
    """Send a plain-text email through the user's Gmail account.

    This is irreversible -- the user is always asked to confirm before it
    runs, with no override. Plain text only: no HTML formatting, attachments,
    or reply-threading.
    """
    service, error = _gmail_service()
    if error:
        return {"ok": False, "error": error}

    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    if cc:
        message["cc"] = cc

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

    try:
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
    except Exception as exc:
        return {"ok": False, "error": f"Could not send email: {exc}"}

    return {"id": sent.get("id"), "to": to, "subject": subject}
