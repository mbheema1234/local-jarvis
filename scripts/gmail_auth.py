"""One-time interactive Gmail authorization.

The agent's tool-calling loop cannot complete this itself -- Google's OAuth
consent screen needs a real browser window and a human clicking through it.
Run this once, by hand, after setting GOOGLE_CLIENT_ID and
GOOGLE_CLIENT_SECRET in .env (see the "Gmail setup" section in README.md for
how to create those in Google Cloud Console).

It opens your browser, you sign in and consent to the two Gmail scopes Jarvis
asks for (read + send), and the resulting token is saved to
data/gmail_token.json -- from then on jarvis/tools/email.py refreshes it
automatically and every run of Jarvis can use it without you doing this again.

    uv run python scripts/gmail_auth.py
"""

from __future__ import annotations

import sys

from jarvis.config import GMAIL_TOKEN_PATH, google_client_id, google_client_secret
from jarvis.tools.email import SCOPES


def main() -> int:
    try:
        client_id = google_client_id()
        client_secret = google_client_secret()
    except RuntimeError as exc:
        print(f"  {exc}")
        return 1

    from google_auth_oauthlib.flow import InstalledAppFlow

    # A "Desktop app" OAuth client (not "Web application") is required here --
    # it's the type that supports the loopback redirect run_local_server uses
    # without a fixed redirect URI having to be registered in Cloud Console.
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    print("  Opening your browser to sign in and authorize Gmail access...")
    creds = flow.run_local_server(port=0)

    GMAIL_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    # Confirm which account this actually authorized, so a mistaken login
    # (wrong Google account signed in) is obvious immediately.
    try:
        from googleapiclient.discovery import build

        profile = (
            build("gmail", "v1", credentials=creds, cache_discovery=False)
            .users()
            .getProfile(userId="me")
            .execute()
        )
        account = profile.get("emailAddress", "unknown")
    except Exception as exc:
        account = f"unknown (could not confirm, but the token was saved -- {exc})"

    print(f"\n  Gmail is now authorized for: {account}")
    print(f"  Token saved to: {GMAIL_TOKEN_PATH}")
    print("  Jarvis can now use search_emails, read_email, and send_email.")
    return 0


sys.exit(main())
