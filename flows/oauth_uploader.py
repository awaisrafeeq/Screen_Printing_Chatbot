# oauth_uploader.py
import os
from typing import Optional, Tuple
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


# Scope lets us create files you own
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

def _get_creds() -> Credentials:
    """
    Uses OAuth 'Installed App' flow. It stores/loads a token at TOKEN_JSON (default: token.json).
    Requires client secret JSON: GOOGLE_OAUTH_CLIENT_SECRET_JSON env var.
    """
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_JSON")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
    token_path = os.getenv("GOOGLE_OAUTH_TOKEN_JSON", "token.json")

    if not client_secret:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_SECRET_JSON is not set. Check your .env file or Render environment variables.")

    # Determine if client_secret is a file path or JSON string
    try:
        # Try parsing as JSON (for Render)
        client_config = json.loads(client_secret)
    except (json.JSONDecodeError, TypeError):
        # Assume it's a file path (for local development)
        if not os.path.isfile(client_secret):
            raise RuntimeError(f"Client secret JSON file not found at: {client_secret}")
        with open(client_secret, "r") as f:
            client_config = json.load(f)

    # Load credentials
    creds = None
    if refresh_token:
        # Use refresh token for non-interactive authentication (Render)
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_config["installed"]["client_id"],
            client_secret=client_config["installed"]["client_secret"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES
        )
    else:
        # Local development: load from token.json or run OAuth flow
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                from google.auth.transport.requests import Request
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
                creds = flow.run_local_server(port=0)
                with open(token_path, "w") as f:
                    f.write(creds.to_json())

    return creds

def upload_to_drive(local_path: str,
                    filename: Optional[str] = None,
                    parent_folder_id: Optional[str] = None,
                    make_public: bool = False) -> Tuple[str, Optional[str]]:
    if not os.path.isfile(local_path):
        raise FileNotFoundError(f"Logo file not found: {local_path}")

    creds = _get_creds()
    service = build("drive", "v3", credentials=creds)

    metadata = {"name": filename or os.path.basename(local_path)}
    if parent_folder_id:
        metadata["parents"] = [parent_folder_id]

    media = MediaFileUpload(local_path, resumable=True)
    file = service.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink"
    ).execute()

    file_id = file["id"]
    link = file.get("webViewLink")

    if make_public:
        # Your personal Drive may or may not allow public links. If allowed:
        try:
            service.permissions().create(
                fileId=file_id,
                body={"role": "reader", "type": "anyone"},
            ).execute()
            file = service.files().get(fileId=file_id, fields="webViewLink").execute()
            link = file.get("webViewLink")
        except Exception:
            pass

    print(f"[Drive-OAuth] Uploaded → id={file_id}, link={link or '(no link)'}")
    return file_id, link
