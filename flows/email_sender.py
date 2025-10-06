# email_sender.py
import os
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv  # pip install python-dotenv
load_dotenv()

def send_email(to_email: str, subject: str, body: str) -> bool:
    """
    Minimal SMTP sender.
    Configure these env vars:
      EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASS, EMAIL_FROM
    Examples (Gmail):
      EMAIL_HOST=smtp.gmail.com
      EMAIL_PORT=587
      EMAIL_USER=youraddress@gmail.com
      EMAIL_PASS=your_app_password   (use an App Password)
      EMAIL_FROM=Screen Printing NW <youraddress@gmail.com>
    """
    host = os.getenv("EMAIL_HOST")
    port = int(os.getenv("EMAIL_PORT", "587"))
    user = os.getenv("EMAIL_USER")
    pwd  = os.getenv("EMAIL_PASS")
    from_addr = os.getenv("EMAIL_FROM", user or "")

    if not (host and port and user and pwd and from_addr and to_email):
        return False

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, pwd)
            smtp.send_message(msg)
        return True
    except Exception:
        return False
