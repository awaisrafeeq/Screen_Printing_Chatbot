# oauth_uploader.py
import os
from typing import Optional, Tuple

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
    if not client_secret or not os.path.isfile(client_secret):
        raise RuntimeError("Set GOOGLE_OAUTH_CLIENT_SECRET_JSON to your downloaded OAuth client secret .json")

    token_path = os.getenv("GOOGLE_OAUTH_TOKEN_JSON", "token.json")
    creds = None

    # Load previously saved token if present
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # If no (valid) token, run the local server OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
            # This opens a browser the first time; after that the token.json will be reused
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
