"""Google Drive authentication helpers."""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def oauth_credentials(credentials_path: str, token_path: str) -> Credentials:
    creds: Credentials | None = None
    token_file = Path(token_path)
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(token_path, DRIVE_SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, DRIVE_SCOPES)
        try:
            creds = flow.run_local_server(port=0, open_browser=False)
        except Exception:
            creds = flow.run_console()
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return creds


def service_account_credentials(service_account_json_path: str):
    return service_account.Credentials.from_service_account_file(
        service_account_json_path,
        scopes=DRIVE_SCOPES,
    )
