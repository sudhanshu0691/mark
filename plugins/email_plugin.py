import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from core.result import done, failed

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


_ENV_PATH = Path(__file__).resolve().parent.parent / "config" / ".env"
if load_dotenv is not None:
    load_dotenv(_ENV_PATH)

DECLARATION = {
    "name": "send_email",
    "description": (
        "Send an email. Always ask the user for explicit confirmation immediately "
        "before calling this tool with confirmed=true."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "receiver": {"type": "STRING", "description": "Recipient email address"},
            "subject": {"type": "STRING", "description": "Email subject"},
            "body": {"type": "STRING", "description": "Email body"},
            "confirmed": {
                "type": "BOOLEAN",
                "description": "Set true only after the user explicitly confirms this exact email",
            },
        },
        "required": ["receiver", "subject", "body", "confirmed"],
    }
}


def run(args, ctx):
    receiver = str(args.get("receiver", "")).strip()
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", "")).strip()

    if not receiver or "@" not in receiver:
        return failed("Email not sent", "receiver must be a valid email address")
    if not subject:
        return failed("Email not sent", "subject is empty")
    if not body:
        return failed("Email not sent", "body is empty")
    if args.get("confirmed") is not True:
        return failed(
            "Email not sent - confirmation required",
            "ask the user to confirm the exact recipient, subject, and body, "
            "then call again with confirmed=true",
        )

    sender = os.getenv("SMTP_EMAIL", "").strip()
    from core.secrets import get_secret

    password = get_secret("smtp_password")
    server_name = os.getenv("SMTP_SERVER", "smtp.gmail.com").strip()
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError:
        return failed("Email not sent", "SMTP_PORT must be a number")
    if not sender or not password:
        return failed(
            "Email not sent",
            "SMTP_EMAIL and an SMTP password are required; save the password "
            "through the OS credential store or configure SMTP_PASSWORD temporarily",
        )

    message = EmailMessage()
    message["From"] = sender
    message["To"] = receiver
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(server_name, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        return failed("Email not sent", f"{type(error).__name__}: {error}")

    return done(f"Email sent to {receiver}", evidence="SMTP accepted the message")