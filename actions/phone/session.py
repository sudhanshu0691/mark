"""
One answered call: disclosure -> listen -> think -> speak, with recording and a final summary.

Everything environment-specific (audio devices, speech-to-text, text-to-speech, the language model, hanging up) is
injected, so the logic below is unit-tested without a phone, a sound card or a GPU.

Safety rules that live in CODE (not in a prompt the model could ignore):
  * The first thing said is a fixed disclosure: it is an AI assistant AND the call is recorded.
  * Recording starts only after the disclosure was spoken; if the caller objects to recording, audio is deleted and the call ends.
  * Asks for OTP / PIN / password / card / bank details get a fixed refusal; the model never sees or says such data.
  * Hard limits: max duration, silence timeout, and a stop() kill-switch.
"""
from __future__ import annotations

import json
import re
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

SENSITIVE = re.compile(r"\b(otp|one[- ]time|pin\b|password|passcode|cvv|card number|credit card|debit card|bank account|account number|"
                       r"ifsc|upi pin|aadhaar|ssn|social security|net ?banking|verification code)\b", re.I)
NO_RECORD = re.compile(r"(don'?t|do not|stop|no|not)\s+(want\s+)?(you\s+to\s+)?(record|recording)|record\s+(mat|nahi)|recording\s+(band|off|stop)", re.I)
END_TOKEN = "[END]"


class AudioIO(Protocol):
    def listen(self, timeout_s: float) -> tuple[bytes, int] | None: ...      # one utterance of caller speech (int16 mono PCM, rate) or None on silence
    def say(self, pcm: bytes, rate: int) -> None: ...                         # play to the call's microphone
    def close(self) -> None: ...


@dataclass
class CallConfig:
    user_name: str = "the owner"
    assistant_name: str = "Mark"
    language: str = "en"                      # "en" | "hi"
    purpose: str = ""
    max_seconds: float = 600.0
    silence_timeout_s: float = 20.0
    max_turns: int = 40
    out_dir: Path = Path("data/calls")


def disclosure(cfg: CallConfig) -> str:
    if cfg.language == "hi":
        return (f"Namaste, main {cfg.user_name} ka AI assistant {cfg.assistant_name} bol raha hu. "
                f"Ye call record ho rahi hai. Bataiye, main kya madad kar sakta hu?")
    return (f"Hello, this is {cfg.assistant_name}, an AI assistant answering for {cfg.user_name}. "
            f"This call is being recorded. How can I help you?")


def system_prompt(cfg: CallConfig) -> str:
    return (
        f"You are {cfg.assistant_name}, an AI assistant answering a phone call for {cfg.user_name} while they are away. "
        f"The caller already heard that you are an AI and that the call is recorded. "
        f"{('Your job on this call: ' + cfg.purpose + '. ') if cfg.purpose else ''}"
        "Rules: speak in the caller's language (English or Hinglish). Keep every reply to ONE or TWO short sentences. "
        "Find out who is calling and what they need, take a clear message, and ask for a callback number if useful. "
        f"{cfg.user_name} is not available; never promise anything on their behalf, never agree to pay or sign anything. "
        "Never give personal details, passwords, codes or financial information. If asked something you do not know, say you will pass the question on. "
        f"When the caller is finished or says goodbye, thank them, say a short goodbye and end your reply with {END_TOKEN}."
    )


@dataclass
class CallResult:
    folder: Path
    transcript: list[tuple[str, str]]
    summary: str
    reason: str
    recorded: bool
    seconds: float


class CallSession:
    def __init__(self, cfg: CallConfig, caller_label: str, audio: AudioIO,
                 stt: Callable[[bytes, int], str], llm: Callable[[str, str], str],
                 tts: Callable[[str, str], tuple[bytes, int]], hangup: Callable[[], object],
                 clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print):
        self.cfg, self.caller, self.audio = cfg, caller_label, audio
        self.stt, self.llm, self.tts, self.hangup = stt, llm, tts, hangup
        self.clock, self.log = clock, log
        self.transcript: list[tuple[str, str]] = []
        self._stop = threading.Event()
        self._stop_reason = ""
        self._rec_caller = bytearray()
        self._rec_mark = bytearray()
        self._rate_caller = self._rate_mark = 16000
        self.recording = False
        self.folder = self._make_folder()

    # ---- control ------------------------------------------------------------------
    def stop(self, reason: str = "stopped by user") -> None:
        """Kill-switch: ends the call at the next opportunity."""
        self._stop_reason = reason
        self._stop.set()

    def _make_folder(self) -> Path:
        safe = re.sub(r"[^A-Za-z0-9]+", "_", self.caller).strip("_")[:30] or "caller"
        f = Path(self.cfg.out_dir) / f"{time.strftime('%Y%m%d_%H%M%S')}_{safe}"
        f.mkdir(parents=True, exist_ok=True)
        return f

    # ---- speaking / hearing ---------------------------------------------------------
    def _say(self, text: str) -> None:
        pcm, rate = self.tts(text, self.cfg.language)
        self.audio.say(pcm, rate)
        if self.recording:
            self._rec_mark += pcm
            self._rate_mark = rate
        self.transcript.append((self.cfg.assistant_name, text))
        self.log(f"[call] {self.cfg.assistant_name}: {text}")

    def _hear(self, timeout: float) -> str | None:
        got = self.audio.listen(timeout)
        if got is None:
            return None
        pcm, rate = got
        if self.recording:
            self._rec_caller += pcm
            self._rate_caller = rate
        text = (self.stt(pcm, rate) or "").strip()
        if text:
            self.transcript.append(("Caller", text))
            self.log(f"[call] Caller: {text}")
        return text or None

    # ---- the call -----------------------------------------------------------------
    def run(self) -> CallResult:
        t0 = self.clock()
        reason = "caller finished"
        try:
            self._say(disclosure(self.cfg))          # fixed words, spoken BEFORE any recording starts
            self.recording = True
            silent = 0
            history: list[str] = []
            for _ in range(self.cfg.max_turns):
                if self._stop.is_set():
                    reason = self._stop_reason
                    break
                if self.clock() - t0 > self.cfg.max_seconds:
                    reason = "time limit reached"
                    self._say("I have to end the call now. I will pass your message on. Goodbye.")
                    break
                heard = self._hear(self.cfg.silence_timeout_s)
                if self._stop.is_set():                       # kill-switch pressed while listening
                    reason = self._stop_reason
                    break
                if heard is None:
                    silent += 1
                    if silent >= 2:
                        reason = "caller silent"
                        self._say("I can't hear you, so I'll end the call. Please call again. Goodbye.")
                        break
                    self._say("Are you still there?")
                    continue
                silent = 0
                if NO_RECORD.search(heard):
                    self._discard_audio()
                    self._say("Understood, I will not record this call. Please call back later or message directly. Goodbye.")
                    reason = "caller refused recording - audio deleted"
                    break
                if SENSITIVE.search(heard):
                    self._say("I can't share or take codes, passwords or financial details on a call. "
                              "Please contact the person directly.")
                    history.append("Caller: (asked for sensitive information - refused, content hidden)")   # the model never sees it
                    history.append(f"{self.cfg.assistant_name}: (refused sensitive request)")
                    continue
                history.append(f"Caller: {heard}")
                reply = (self.llm(system_prompt(self.cfg), "\n".join(history[-14:]) + f"\n{self.cfg.assistant_name}:") or "").strip()
                end = END_TOKEN in reply
                reply = reply.replace(END_TOKEN, "").strip() or "Thank you, goodbye."
                history.append(f"{self.cfg.assistant_name}: {reply}")
                self._say(reply)
                if end:
                    break
            else:
                reason = "turn limit reached"
        except Exception as e:                                   # never leave a call hanging on a bug
            reason = f"error: {type(e).__name__}: {e}"
            self.log(f"[call] ERROR {reason}")
        finally:
            try:
                self.hangup()
            except Exception as e:
                self.log(f"[call] hangup failed: {e}")
            try:
                self.audio.close()
            except Exception:
                pass
        return self._finish(reason, self.clock() - t0)

    # ---- saving -------------------------------------------------------------------
    def _discard_audio(self) -> None:
        self._rec_caller.clear()
        self._rec_mark.clear()
        self.recording = False

    def _write_wav(self, name: str, pcm: bytearray, rate: int) -> bool:
        if not pcm:
            return False
        with wave.open(str(self.folder / name), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(bytes(pcm))
        return True

    def _finish(self, reason: str, seconds: float) -> CallResult:
        recorded = False
        if self.recording:
            a = self._write_wav("caller.wav", self._rec_caller, self._rate_caller)
            b = self._write_wav("mark.wav", self._rec_mark, self._rate_mark)
            recorded = a or b
        text = "\n".join(f"**{who}:** {t}" for who, t in self.transcript)
        safe_text = "\n".join(f"**{who}:** " + ("(sensitive request, redacted)" if who == "Caller" and SENSITIVE.search(t) else t)
                              for who, t in self.transcript)
        try:
            summary = (self.llm("Summarise this phone call for the owner in 3 short bullet points: who called, what they want, "
                                "what to do next. Reply in the language of the call.", safe_text) or "").strip()
        except Exception:
            summary = ""
        summary = summary or "(summary unavailable - read the transcript)"
        (self.folder / "transcript.md").write_text(f"# Call with {self.caller}\n\n{text}\n", encoding="utf-8")
        (self.folder / "summary.md").write_text(f"# Summary - {self.caller}\n\n{summary}\n\nEnded: {reason}\n", encoding="utf-8")
        (self.folder / "meta.json").write_text(json.dumps({
            "caller": self.caller, "seconds": round(seconds, 1), "ended": reason, "recorded": recorded,
            "disclosure_spoken": bool(self.transcript), "started": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=2), encoding="utf-8")
        return CallResult(self.folder, self.transcript, summary, reason, recorded, seconds)
