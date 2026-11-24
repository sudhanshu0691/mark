import threading
from core.plugin_process import run_isolated

def test_isolated_worker_does_not_inherit_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-secret")
    p = tmp_path / "sample.py"
    p.write_text('import os\nMETADATA={"execution":"process"}\nDECLARATION={"name":"sample"}\ndef run(args,ctx): return str(os.getenv("GROQ_API_KEY"))\n', encoding="utf-8")
    assert run_isolated(p, "sample", {}, 10, threading.Event()) == "None"

def test_isolated_worker_is_terminated(tmp_path):
    p = tmp_path / "slow.py"
    p.write_text('import time\nMETADATA={"execution":"process"}\nDECLARATION={"name":"slow"}\ndef run(args,ctx): time.sleep(30)\n', encoding="utf-8")
    assert run_isolated(p, "slow", {}, .3, threading.Event()).startswith("[UNVERIFIED]")


def test_cancelled_plugin_never_launches(tmp_path):
    cancel = threading.Event()
    cancel.set()
    assert run_isolated(tmp_path / "missing.py", "sample", {}, 10, cancel).startswith("[CANCELLED]")
