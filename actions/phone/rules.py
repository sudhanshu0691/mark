"""Armed rules: Mark answers a call ONLY if the user armed it by command and the caller matches a name/number they gave."""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONTACTS = BASE_DIR / "config" / "contacts.json"
MAX_MINUTES = 12 * 60


def normalize_number(s: str) -> str:
    """Digits only, compared by the last 10 digits so +91 98765 43210 == 09876543210."""
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 7 else ""


def load_contacts(path: Path = CONTACTS) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("contacts", {})
        return {str(k).strip().lower(): str(v) for k, v in raw.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def _words(s: str) -> str:
    return " " + re.sub(r"[^\w]+", " ", s.lower()).strip() + " "


def match_caller(texts: list[str], targets: list[str], contacts: dict[str, str] | None = None) -> str | None:
    """Return the matched target label, or None. A target is a name or a phone number.
    A name also matches the number stored for it in contacts.json, and a number matches its saved name."""
    contacts = contacts or {}
    blob = " ".join(texts)
    blob_words = _words(blob)
    blob_digits = [normalize_number(m) for m in re.findall(r"\+?\d[\d\s().-]{6,}\d", blob)]
    for t in targets:
        t = t.strip()
        if not t:
            continue
        num = normalize_number(t)
        if num and not re.search(r"[a-zA-Z]", t):
            if num in blob_digits:
                return t
            for name, phone in contacts.items():                 # number given, caller shown by saved name
                if normalize_number(phone) == num and _words(name) in blob_words:
                    return t
        else:
            if _words(t) in blob_words:
                return t
            phone = contacts.get(t.lower())                      # name given, caller shown as a bare number
            if phone and normalize_number(phone) in blob_digits:
                return t
    return None


@dataclass
class Rule:
    targets: list[str]
    expires: float
    purpose: str = ""
    created: float = field(default_factory=time.time)
    handled: int = 0


class Armed:
    """The one switch that allows call answering. Nothing happens unless a Rule is active."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._rule: Rule | None = None

    def arm(self, targets: list[str], minutes: float = 120, purpose: str = "") -> Rule:
        targets = [t.strip() for t in targets if t and t.strip()]
        if not targets:
            raise ValueError("name at least one caller (a name or a phone number)")
        minutes = max(1.0, min(float(minutes), MAX_MINUTES))
        with self._lock:
            self._rule = Rule(targets, self._clock() + minutes * 60, purpose.strip())
            return self._rule

    def disarm(self) -> bool:
        with self._lock:
            had = self._rule is not None
            self._rule = None
            return had

    def active(self) -> Rule | None:
        with self._lock:
            if self._rule and self._clock() >= self._rule.expires:
                self._rule = None
            return self._rule

    def check(self, note: dict, contacts: dict[str, str] | None = None) -> tuple[Rule, str] | None:
        """Is this notification a call we were told to answer?"""
        rule = self.active()
        if rule is None or not note.get("is_call"):
            return None
        label = match_caller(list(note.get("texts") or []), rule.targets, contacts if contacts is not None else load_contacts())
        return (rule, label) if label else None

    def describe(self) -> str:
        r = self.active()
        if not r:
            return "not armed - Mark will NOT answer any call"
        left = int((r.expires - self._clock()) / 60)
        return f"armed for {', '.join(r.targets)} for {left} more minutes" + (f" (purpose: {r.purpose})" if r.purpose else "")
