"""Safe persistence helpers for the Qubo client session."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


SESSION_PATH = Path.home() / ".qubopy_session.json"


def save_session(auth: Any) -> None:
    tokens = getattr(auth, "tokens", None)
    if tokens is None:
        raise RuntimeError("Qubo authentication returned no session tokens")
    payload = {
        "accessToken": tokens.access_token,
        "refreshToken": tokens.refresh_token,
        "uuid": tokens.user_uuid,
    }
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(SESSION_PATH.parent),
        prefix=f".{SESSION_PATH.name}.tmp.",
    )
    temporary_path = Path(temporary)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with open(fd, "w", encoding="utf-8", closefd=True) as stream:
            json.dump(payload, stream, indent=2)
        temporary_path.replace(SESSION_PATH)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def load_session(auth: Any) -> bool:
    try:
        payload = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        access_token = str(payload["accessToken"]).strip()
        user_uuid = str(payload["uuid"]).strip()
        if not access_token or not user_uuid:
            return False
        auth.set_tokens(
            access_token=access_token,
            refresh_token=payload.get("refreshToken"),
            user_uuid=user_uuid,
        )
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False
