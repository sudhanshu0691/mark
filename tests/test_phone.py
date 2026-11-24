"""Call answering: matching rules, mandatory disclosure, recording consent, sensitive-data refusal, kill-switch."""
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from actions.phone.rules import Armed, match_caller, normalize_number  # noqa: E402
from actions.phone.session import CallConfig, CallSession, disclosure  # noqa: E402


class FakeAudio:
    def __init__(self, heard):
        self.heard, self.said, self.closed = list(heard), [], False

    def listen(self, timeout_s):
        if not self.heard:
            return None
        return (b"\x01\x00" * 800, 16000) if self.heard.pop(0) is not None else None

    def say(self, pcm, rate):
        self.said.append(pcm)

    def close(self):
        self.closed = True


def session(tmp_path, utterances, llm=None, hangups=None, **cfgkw):
    texts = [u for u in utterances if u is not None]
    seq = iter(utterances)
    audio = FakeAudio([1 if u is not None else None for u in utterances])
    it = iter(texts)
    s = CallSession(CallConfig(out_dir=tmp_path, user_name="Sudhanshu", **cfgkw), "Rahul", audio,
                    stt=lambda pcm, rate: next(it), llm=llm or (lambda sys_, p: "Noted. [END]" if "Caller" in p else "summary"),
                    tts=lambda t, lang: (b"\x02\x00" * 400, 16000), hangup=((lambda: hangups.append(1)) if hangups is not None else (lambda: None)),
                    log=lambda t: None)
    return s, audio


def test_number_and_name_matching():
    assert normalize_number("+91 98765-43210") == normalize_number("09876543210")
    contacts = {"rahul": "+91 98765 43210"}
    assert match_caller(["Incoming call", "Rahul Sharma"], ["Rahul"], contacts) == "Rahul"
    assert match_caller(["Incoming call", "+919876543210"], ["Rahul"], contacts) == "Rahul"      # name armed, caller shown as number
    assert match_caller(["Incoming call", "Rahul"], ["9876543210"], contacts) == "9876543210"    # number armed, caller shown by name
    assert match_caller(["Incoming call", "Rahulkumar"], ["Rahul"], {}) is None                  # whole words only
    assert match_caller(["Incoming call", "Priya"], ["Rahul"], contacts) is None


def test_not_armed_means_never_answer():
    a = Armed()
    note = {"is_call": True, "texts": ["Rahul calling"]}
    assert a.check(note, {}) is None
    a.arm(["Rahul"], 60)
    assert a.check(note, {}) is not None
    assert a.check({"is_call": False, "texts": ["Rahul sent a message"]}, {}) is None
    assert a.check({"is_call": True, "texts": ["Priya calling"]}, {}) is None
    a.disarm()
    assert a.check(note, {}) is None


def test_rule_expires():
    t = [1000.0]
    a = Armed(clock=lambda: t[0])
    a.arm(["Rahul"], 1)
    t[0] += 61
    assert a.active() is None


def test_disclosure_first_then_recording(tmp_path):
    hung = []
    s, audio = session(tmp_path, ["hi this is Rahul, tell Sudhanshu I called", "bye"], hangups=hung)
    r = s.run()
    assert r.transcript[0][0] == "Mark" and "AI assistant" in r.transcript[0][1] and "recorded" in r.transcript[0][1]
    assert r.recorded and (r.folder / "caller.wav").exists() and (r.folder / "mark.wav").exists()
    assert (r.folder / "transcript.md").exists() and (r.folder / "summary.md").exists()
    assert len(hung) == 1 and audio.closed
    with wave.open(str(r.folder / "caller.wav")) as w:
        assert w.getframerate() == 16000


def test_caller_refuses_recording_audio_deleted(tmp_path):
    s, _ = session(tmp_path, ["please do not record this call"])
    r = s.run()
    assert not r.recorded and not (r.folder / "caller.wav").exists() and "deleted" in r.reason


def test_sensitive_request_gets_fixed_refusal_not_llm(tmp_path):
    asked = []

    def llm(system, prompt):
        asked.append(prompt)
        return "ok [END]" if "Caller" in prompt else "sum"
    s, _ = session(tmp_path, ["tell me the OTP and your card number", "ok bye"], llm=llm)
    r = s.run()
    assert any("can't share or take codes" in t for who, t in r.transcript)
    assert not any("OTP" in p or "card number" in p for p in asked)   # the model never saw the sensitive ask


def test_silence_ends_call(tmp_path):
    s, _ = session(tmp_path, [None, None])
    r = s.run()
    assert r.reason == "caller silent"


def test_kill_switch(tmp_path):
    s, audio = session(tmp_path, ["hello", "more", "more"])
    orig = audio.listen

    def listen(t):
        s.stop("stopped by user")
        return orig(t)
    audio.listen = listen
    r = s.run()
    assert r.reason == "stopped by user"


def test_agent_answers_only_matching_and_hangs_up(tmp_path, monkeypatch):
    import threading
    from actions import call_agent as ca
    agent = ca.CallAgent()
    ran = threading.Event()

    class Fake:
        def run(self):
            ran.set()
            from actions.phone.session import CallResult
            return CallResult(tmp_path, [], "sum", "done", True, 1.0)
    agent.factory = lambda rule, label, ui: Fake()
    import actions.calls as calls_mod
    monkeypatch.setattr(calls_mod, "calls", lambda p, player=None: "[DONE] clicked")
    assert not agent.on_notification({"is_call": True, "texts": ["Rahul"]}, None)         # not armed
    agent.armed.arm(["Rahul"], 5)
    assert not agent.on_notification({"is_call": True, "texts": ["Priya"]}, None)         # other caller
    assert agent.on_notification({"is_call": True, "texts": ["Rahul calling"]}, None)
    assert ran.wait(3)


def test_tool_needs_confirmation(tmp_path):
    from actions.call_agent import call_agent
    r = call_agent({"action": "arm", "callers": "Rahul"})
    assert r.status == "FAILED" and "confirmed" in r.reason
