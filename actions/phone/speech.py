"""Local speech-to-text (faster-whisper), text-to-speech (Piper if configured, else edge-tts), and the language model hook."""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import wave
from pathlib import Path
from threading import Lock

import numpy as np

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG = BASE_DIR / "config" / "phone.json"
DEFAULTS = {
    "user_name": "", "assistant_name": "Mark", "language": "en", "whisper_model": "small", "cable_output": "CABLE Input",
    "edge_voice_en": "en-US-GuyNeural", "edge_voice_hi": "hi-IN-MadhurNeural",
    "piper_exe": "", "piper_voice_en": "", "piper_voice_hi": "",
    "max_seconds": 600, "silence_timeout_s": 20,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    if not cfg.get("user_name"):
        try:
            from memory.config_manager import get_user_name
            cfg["user_name"] = get_user_name() or "the owner"
        except Exception:
            cfg["user_name"] = "the owner"
    return cfg


_model_lock = Lock()
_model = None
_model_name = None


def make_stt(cfg: dict):
    def stt(pcm: bytes, rate: int) -> str:
        global _model, _model_name
        from faster_whisper import WhisperModel
        from actions.phone.audio import _to_mono16k
        name = cfg.get("whisper_model", "small")
        with _model_lock:
            if _model is None or name != _model_name:
                _model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=4,
                                      local_files_only=True)
                _model_name = name
            audio = _to_mono16k(pcm, 1, rate) / 32768.0
            if not len(audio):
                return ""
            segs, _info = _model.transcribe(audio, vad_filter=True, beam_size=1, condition_on_previous_text=False)
            return " ".join(s.text.strip() for s in segs).strip()
    return stt


def make_tts(cfg: dict):
    def tts(text: str, lang: str) -> tuple[bytes, int]:
        exe, voice = cfg.get("piper_exe"), cfg.get(f"piper_voice_{lang}") or ""
        if exe and voice and Path(exe).exists():
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "reply.wav"
                subprocess.run([exe, "--model", voice, "--output_file", str(out)], input=text.encode("utf-8"), check=True, timeout=60)
                with wave.open(str(out), "rb") as w:
                    return w.readframes(w.getnframes()), w.getframerate()
        import edge_tts
        import miniaudio
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = Path(tmp) / "reply.mp3"
            asyncio.run(edge_tts.Communicate(text, cfg.get(f"edge_voice_{lang}") or cfg["edge_voice_en"]).save(str(mp3)))
            d = miniaudio.decode_file(str(mp3), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
            return d.samples.tobytes(), 24000
    return tts


def make_llm():
    from core import llm

    def ask(system: str, prompt: str) -> str:
        return llm.generate(prompt, role="text", system=system, temperature=0.4, max_tokens=160)
    return ask
