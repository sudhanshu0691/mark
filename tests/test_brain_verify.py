"""The verifier is enforced by the brain - the model cannot skip it."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import brain, verify  # noqa: E402


def deps(tmp: Path, windows=(), vision_answer="UNSURE: no idea", shot=True):
    return verify.Deps(
        list_windows=lambda: list(windows),
        resolve_path=lambda raw: Path(raw),
        dispatch=lambda n, a: "[UNVERIFIED] n/a",
        screenshot=(lambda: b"png") if shot else None,
        vision=lambda img, q: vision_answer,
        save_proof=lambda img, tag: str(tmp / "p.png"),
        sleep=lambda s: None,
    )


def scripted(*steps):
    it = iter(steps)

    def chat(messages, decls, system=None):
        return next(it)
    return chat


def call(name, **args):
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def final(text):
    return {"content": text}


def test_file_claimed_done_but_missing_becomes_failed(tmp_path):
    target = tmp_path / "a.txt"                                   # never created
    out = brain.run_goal(
        "make a.txt", declarations=[], log=lambda s: None, verifier=deps(tmp_path),
        dispatch=lambda n, a: "[DONE] wrote file",
        chat=scripted(call("file_controller", action="write", path=str(target)), final("RESULT: SUCCESS\nDONE: a.txt")))
    assert "RESULT: PARTIAL" in out and "FAIL via direct" in out


def test_file_really_there_is_verified(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")
    out = brain.run_goal(
        "make a.txt", declarations=[], log=lambda s: None, verifier=deps(tmp_path),
        dispatch=lambda n, a: "[DONE] wrote file",
        chat=scripted(call("file_controller", action="write", path=str(target)), final("RESULT: SUCCESS\nDONE: a.txt")))
    assert "RESULT: SUCCESS" in out and "PASS via direct" in out


def test_app_not_in_window_list_is_failed(tmp_path):
    out = brain.run_goal(
        "open notepad", declarations=[], log=lambda s: None,
        verifier=deps(tmp_path, windows=[{"title": "Chrome", "exe": "chrome.exe"}]),
        dispatch=lambda n, a: "[DONE] opened",
        chat=scripted(call("open_app", app_name="Notepad"), final("RESULT: SUCCESS\nDONE: opened")))
    assert "RESULT: PARTIAL" in out


def test_vision_no_downgrades_success(tmp_path):
    out = brain.run_goal(
        "change theme", declarations=[], log=lambda s: None,
        verifier=deps(tmp_path, vision_answer="NO: still light theme"),
        dispatch=lambda n, a: "[UNVERIFIED] ran",
        chat=scripted(call("computer_settings", action="dark"), final("RESULT: SUCCESS\nDONE: theme")))
    assert "RESULT: PARTIAL" in out and "NO" in out.upper()


def test_unverifiable_success_is_downgraded(tmp_path):
    out = brain.run_goal(
        "x", declarations=[], log=lambda s: None, verifier=deps(tmp_path, shot=False),
        dispatch=lambda n, a: "[UNVERIFIED] ran",
        chat=scripted(call("computer_settings", action="dark"), final("RESULT: SUCCESS\nDONE: theme")))
    assert "RESULT: PARTIAL" in out and "not independently verified" in out


def test_vision_yes_upgrades_unverified(tmp_path):
    d = deps(tmp_path, vision_answer="YES: dark theme visible")
    v = verify.verify_step("computer_settings", {"action": "dark"}, d)
    assert v.ok and v.method == "vision"


def test_model_down_is_reported_honestly(tmp_path):
    from core import llm

    def boom(*a, **k):
        raise llm.LLMUnavailable("provider unavailable")
    out = brain.run_goal("x", declarations=[], log=lambda s: None, verifier=deps(tmp_path),
                         dispatch=lambda n, a: "", chat=boom)
    assert out.startswith("RESULT: FAILED") and "provider unavailable" in out
