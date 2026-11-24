"""Real call audio on Windows (free tools only).

  hearing the caller : WASAPI loopback of the default speakers (what the call app plays)  -> pyaudiowpatch
  speaking to caller : we play Mark' voice into the virtual cable "CABLE Input" (VB-CABLE, free for personal use);
                       the call app's MICROPHONE must be set to "CABLE Output" once (see docs/CALLS.md)
Half duplex: Mark listens, then speaks, then listens - no echo handling needed.
"""
from __future__ import annotations

import time

import numpy as np

TARGET_RATE = 16000


def _to_mono16k(raw: bytes, channels: int, rate: int) -> np.ndarray:
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if channels > 1:
        a = a.reshape(-1, channels).mean(axis=1)
    if rate != TARGET_RATE and len(a) > 1:
        n = int(len(a) * TARGET_RATE / rate)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a)
    return a


class Vad:
    """Tiny energy voice-activity detector: finds the start and end of one spoken utterance."""

    def __init__(self, frame_ms: int = 30, end_silence_ms: int = 900, min_speech_ms: int = 240, max_s: float = 25.0):
        self.frame = int(TARGET_RATE * frame_ms / 1000)
        self.end_frames = end_silence_ms // frame_ms
        self.min_frames = min_speech_ms // frame_ms
        self.max_frames = int(max_s * 1000 / frame_ms)
        self.noise = None

    def rms(self, x: np.ndarray) -> float:
        return float(np.sqrt(np.mean(np.square(x)) + 1e-9))

    def update_noise(self, level: float) -> None:
        self.noise = level if self.noise is None else 0.95 * self.noise + 0.05 * min(level, self.noise * 3 + 50)

    def threshold(self) -> float:
        return max(350.0, (self.noise or 0) * 3.0)


def preflight() -> list[str]:
    """What is missing for real calls? Empty list = ready."""
    missing = []
    try:
        import pyaudiowpatch as pa
    except ImportError:
        return ["pyaudiowpatch is not installed (pip install pyaudiowpatch) - it captures the caller's audio"]
    p = pa.PyAudio()
    try:
        try:
            p.get_default_wasapi_loopback()
        except Exception:
            missing.append("no WASAPI loopback device found for the default speakers")
        if find_output(p, "CABLE Input") is None:
            missing.append('virtual audio cable not found: install VB-CABLE (vb-audio.com/Cable, free) so Mark can speak into the call')
    finally:
        p.terminate()
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        missing.append("faster-whisper is not installed (pip install faster-whisper) - local speech-to-text")
    return missing


def find_output(p, name_part: str):
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        if d.get("maxOutputChannels", 0) > 0 and name_part.lower() in str(d.get("name", "")).lower():
            return d
    return None


class WindowsCallAudio:
    def __init__(self, cable_name: str = "CABLE Input"):
        import pyaudiowpatch as pa
        self.pa = pa
        self.p = pa.PyAudio()
        self.loop = self.p.get_default_wasapi_loopback()
        self.out = find_output(self.p, cable_name)
        if self.out is None:
            raise RuntimeError("virtual audio cable output not found")
        self.vad = Vad()
        self._in = None

    def _open_in(self):
        if self._in is None:
            ch, rate = int(self.loop["maxInputChannels"]), int(self.loop["defaultSampleRate"])
            self._in = self.p.open(format=self.pa.paInt16, channels=ch, rate=rate, input=True,
                                   frames_per_buffer=int(rate * 0.03), input_device_index=self.loop["index"])
            self._in_fmt = (ch, rate)
        return self._in

    def listen(self, timeout_s: float):
        st = self._open_in()
        ch, rate = self._in_fmt
        frame = int(rate * 0.03)
        started, speech, quiet = False, [], 0
        t0 = time.monotonic()
        v = self.vad
        while time.monotonic() - t0 < timeout_s + (25 if started else 0):
            raw = st.read(frame, exception_on_overflow=False)
            x = _to_mono16k(raw, ch, rate)
            lvl = v.rms(x)
            if not started:
                v.update_noise(lvl)
            if lvl > v.threshold():
                started, quiet = True, 0
                speech.append(x)
            elif started:
                speech.append(x)
                quiet += 1
                if quiet >= v.end_frames:
                    break
            if started and len(speech) > v.max_frames:
                break
        if not started or len(speech) < v.min_frames:
            return None
        pcm = np.clip(np.concatenate(speech), -32768, 32767).astype(np.int16).tobytes()
        return pcm, TARGET_RATE

    def say(self, pcm: bytes, rate: int) -> None:
        dev_rate = int(self.out["defaultSampleRate"])
        a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if rate != dev_rate and len(a) > 1:
            n = int(len(a) * dev_rate / rate)
            a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a)
        ch = max(1, min(2, int(self.out["maxOutputChannels"])))
        a = np.repeat(a[:, None], ch, axis=1).astype(np.int16)
        s = self.p.open(format=self.pa.paInt16, channels=ch, rate=dev_rate, output=True, output_device_index=self.out["index"])
        try:
            s.write(a.tobytes())
        finally:
            s.stop_stream(); s.close()
        if self._in is not None:                      # drop what was captured while we spoke
            try:
                self._in.stop_stream(); self._in.start_stream()
            except Exception:
                pass

    def close(self) -> None:
        try:
            if self._in is not None:
                self._in.stop_stream(); self._in.close()
        finally:
            self.p.terminate()
