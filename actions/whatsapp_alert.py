"""Provider-backed WhatsApp alert helper.

The existing desktop automation module is deliberately not used here. Alerts
must go through an authorized provider and require a complete international
number supplied by configuration.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests


ALERT_TEXT = "⚠️ MARK UNKNOWN PERSON ALERT\n\nAn unrecognized person has been detected alone in front of your camera for at least 10 seconds.\n\nDetection time: {timestamp}\n\nStatus: Unknown identity"


def configured_recipient() -> str:
    value = os.getenv("MARK_WHATSAPP_RECIPIENT", "").strip()
    if not re.fullmatch(r"\+[1-9]\d{7,14}", value):
        raise ValueError("MARK_WHATSAPP_RECIPIENT must be a complete international number such as +919876543210")
    return value


def send_alert(snapshot: bytes | None = None, mime_type: str = "image/jpeg") -> str:
    recipient = configured_recipient()
    provider = os.getenv("MARK_WHATSAPP_PROVIDER", "").strip().lower()
    from core.secrets import get_secret

    token = get_secret("whatsapp")
    sender = os.getenv("MARK_WHATSAPP_SENDER_ID", "").strip()
    if provider != "meta" or not token or not sender:
        raise RuntimeError("WhatsApp provider is not configured; no alert was sent")
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": ALERT_TEXT.format(timestamp=datetime.now(timezone.utc).isoformat())},
    }
    response = requests.post(
        f"https://graph.facebook.com/v20.0/{sender}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=15,
    )
    response.raise_for_status()
    message_id = response.json().get("messages", [{}])[0].get("id")
    if not message_id:
        raise RuntimeError("WhatsApp provider did not confirm message acceptance")
    if snapshot:
        media = requests.post(
            f"https://graph.facebook.com/v20.0/{sender}/media",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("unknown-person.jpg", snapshot, mime_type)},
            data={"messaging_product": "whatsapp", "type": mime_type},
            timeout=15,
        )
        media.raise_for_status()
        media_id = media.json().get("id")
        if not media_id:
            raise RuntimeError("WhatsApp provider did not confirm snapshot upload")
        image = requests.post(
            f"https://graph.facebook.com/v20.0/{sender}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"messaging_product": "whatsapp", "to": recipient, "type": "image",
                  "image": {"id": media_id, "caption": "MARK unknown-person event snapshot"}},
            timeout=15,
        )
        image.raise_for_status()
        image_id = image.json().get("messages", [{}])[0].get("id")
        if not image_id:
            raise RuntimeError("WhatsApp provider did not confirm snapshot message")
        return str(image_id)
    return str(message_id)
