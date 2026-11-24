"""
call_agent - answer calls for the user while they are away. OFF by default.

    action=arm      callers (names/numbers), minutes, purpose, confirmed=true   -> start allowing answers for THOSE callers only
    action=disarm                                                                -> stop answering
    action=status                                                                -> is it armed / is a call running
    action=stop                                                                  -> kill-switch: end the running call now
    action=recent   count                                                        -> last calls with their summaries

Nothing is answered unless the user armed it with an explicit command, the caller matches, and the audio setup passed preflight.
"""
from __future__ import annotations

import threading
from pathlib import Path

from core.paths import CALLS_DIR, ensure
from core.result import ActionResult, done, failed, unverified
from actions.phone import audio as _audio
from actions.phone import speech as _speech
from actions.phone.rules import Armed, load_contacts
from actions.phone.session import CallConfig, CallSession


class CallAgent:
    def __init__(self):
        self.armed = Armed()
        self.session: CallSession | None = None
        self._lock = threading.Lock()
        self.factory = self._real_session          # tests replace this

    # ---- notification hook (called from the notification watcher thread) ------------
    def on_notification(self, note: dict, ui=None) -> bool:
        hit = self.armed.check(note, load_contacts())
        if not hit:
            return False
        with self._lock:
            if self.session is not None:
                return False                       # one call at a time
            rule, label = hit
            self.session = object()                # type: ignore[assignment]  (reserved until the real one exists)
            threading.Thread(target=self._handle, args=(rule, label, ui), daemon=True).start()
        return True

    def _log(self, ui, text: str) -> None:
        if ui is not None:
            try:
                ui.write_log(text)
            except Exception:
                pass

    def _handle(self, rule, label: str, ui) -> None:
        try:
            self._log(ui, f"SYS: call from {label} matches the rule - answering")
            if ui is not None and hasattr(ui, "show_work"):
                ui.show_work(f"CALL  -  {label}", ["answering ..."], 10.0)
            from actions.calls import calls
            ans = calls({"action": "answer"}, player=None)
            if not str(ans).startswith("[DONE]"):
                ans = calls({"action": "answer"}, player=None)         # one retry: banners can be slow to expose buttons
            if not str(ans).startswith("[DONE]"):
                self._log(ui, f"ERR: could not answer the call from {label}: {str(ans).splitlines()[0][:120]}")
                return
            session = self.factory(rule, label, ui)
            self.session = session
            result = session.run()
            rule.handled += 1
            head = result.summary.replace("\n", " ")[:300]
            self._log(ui, f"SYS: call with {label} ended ({result.reason}, {result.seconds:.0f}s). Summary: {head}")
            if ui is not None and hasattr(ui, "show_work"):
                ui.show_work(f"CALL ENDED  -  {label}", [result.reason, head, f"saved in {result.folder}"], 12.0)
        except Exception as e:
            self._log(ui, f"ERR: call handling failed: {type(e).__name__}: {e}")
        finally:
            self.session = None

    def _real_session(self, rule, label: str, ui) -> CallSession:
        cfg = _speech.load_config()
        ensure(CALLS_DIR)
        conf = CallConfig(user_name=cfg["user_name"], assistant_name=cfg["assistant_name"], language=cfg["language"],
                          purpose=rule.purpose, max_seconds=float(cfg["max_seconds"]), silence_timeout_s=float(cfg["silence_timeout_s"]),
                          out_dir=CALLS_DIR)
        from actions.calls import calls
        return CallSession(conf, label, _audio.WindowsCallAudio(cfg["cable_output"]), _speech.make_stt(cfg), _speech.make_llm(),
                           _speech.make_tts(cfg), lambda: calls({"action": "hangup"}, player=None),
                           log=lambda t: self._log(ui, t))


AGENT = CallAgent()


def _targets(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    return [t for t in str(v or "").replace(";", ",").split(",")]


def call_agent(parameters: dict | None = None, player=None, **_) -> ActionResult:
    p = parameters or {}
    action = str(p.get("action", "status")).lower().strip()
    try:
        if action == "arm":
            if not p.get("confirmed"):
                return failed("Not armed", "arming needs confirmed=true, set only after the user explicitly asked to answer calls for them")
            targets = _targets(p.get("callers") or p.get("targets"))
            missing = _audio.preflight()
            if missing:
                return failed("Call answering is not ready on this PC", "; ".join(missing) + ". See docs/CALLS.md")
            rule = AGENT.armed.arm(targets, float(p.get("minutes", 120) or 120), str(p.get("purpose", "")))
            return done("Call answering is ON", evidence=AGENT.armed.describe() +
                        ". Mark will say it is an AI and that the call is recorded. Callers not in this list are NOT answered.")
        if action == "disarm":
            had = AGENT.armed.disarm()
            return done("Call answering is OFF" if had else "Call answering was already OFF")
        if action == "status":
            busy = AGENT.session is not None
            return done("Call agent status", evidence=AGENT.armed.describe() + ("; a call is in progress" if busy else ""))
        if action == "stop":
            s = AGENT.session
            if isinstance(s, CallSession):
                s.stop("stopped by user")
                return done("Stopping the call now")
            return failed("No call in progress", "nothing to stop")
        if action == "recent":
            n = max(1, min(int(p.get("count", 5) or 5), 20))
            folders = sorted([d for d in Path(CALLS_DIR).glob("*") if d.is_dir()], reverse=True)[:n]
            if not folders:
                return done("No recorded calls yet")
            out = []
            for f in folders:
                s = (f / "summary.md")
                out.append(f"{f.name}: " + (s.read_text(encoding="utf-8").replace("\n", " ")[:220] if s.exists() else "(no summary)"))
            return done(f"{len(folders)} recent call(s)", evidence="\n".join(out))
        return failed(f"Unknown action '{action}'", "use arm | disarm | status | stop | recent")
    except ValueError as e:
        return failed("Invalid input", str(e))
    except Exception as e:
        return failed("call_agent crashed", f"{type(e).__name__}: {e}")
