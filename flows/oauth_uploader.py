import os
from typing import Optional, Tuple
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request

# Scope lets us create files you own
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

def _get_creds() -> Credentials:
    """
    Uses OAuth flow. Loads token from TOKEN_JSON (default: token.json) or runs OAuth flow.
    Requires GOOGLE_OAUTH_CLIENT_SECRET_JSON env var (file path or JSON string).
    Uses GOOGLE_REFRESH_TOKEN for non-interactive auth in Render.
    """
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_JSON")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
    token_path = os.getenv("GOOGLE_OAUTH_TOKEN_JSON", "token.json")
    creds = None

    print(f"DEBUG: GOOGLE_OAUTH_CLIENT_SECRET_JSON set: {bool(client_secret)}")
    print(f"DEBUG: GOOGLE_REFRESH_TOKEN set: {bool(refresh_token)}")

    if not client_secret:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_SECRET_JSON is not set. Check your .env file or Render environment variables.")

    # Parse client secret (file or JSON string)
    client_config = None
    try:
        # Try parsing as JSON string (for Render)
        client_config = json.loads(client_secret)
    except json.JSONDecodeError:
        # Assume it's a file path (for local development)
        if not os.path.isfile(client_secret):
            raise RuntimeError(f"Client secret JSON file not found at: {client_secret}")
        with open(client_secret, "r") as f:
            client_config = json.load(f)

    # Extract client details (support both "web" and "installed")
    client_type = next((k for k in ["web", "installed"] if k in client_config), None)
    if not client_type:
        raise ValueError("Invalid client configuration: missing 'web' or 'installed' section")
    
    client_id = client_config[client_type].get("client_id")
    client_secret_value = client_config[client_type].get("client_secret")
    if not client_id or not client_secret_value:
        raise ValueError("Invalid client configuration: missing client_id or client_secret")

    # Load previously saved token if present
    if os.path.exists(token_path):
        with open(token_path, "r") as f:
            creds_data = json.load(f)
        creds = Credentials.from_authorized_user_info(creds_data, SCOPES)

    # Use refresh token from env var if provided (e.g., Render)
    if refresh_token:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret_value,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES
        )
        print(f"DEBUG: Initialized creds with refresh_token: {refresh_token[:10]}...")

    # Validate and refresh if needed
    if creds:
        print(f"DEBUG: Initial creds valid: {creds.valid}")
        if not creds.valid and creds.refresh_token:
            try:
                creds.refresh(Request())
                print("DEBUG: Refreshed credentials successfully")
            except Exception as e:
                print(f"DEBUG: Refresh failed: {str(e)}")
        elif not creds.valid:
            print("DEBUG: Creds invalid and no refresh token available")

    # Raise error if still invalid in Render
    if not creds or not creds.valid:
        if not os.getenv("RENDER"):  # Local flow
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)
        else:
            raise RuntimeError("No valid credentials and interactive flow disabled in Render. Set GOOGLE_REFRESH_TOKEN or check refresh_token validity.")
        
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
