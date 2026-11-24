"""Push-to-talk capture and interruptible Windows speech for the desktop assistant."""
import threading
import time


class LocalVoice:
    def __init__(self):
        self._released = threading.Event()
        self._lock = threading.Lock()

    def press(self, ui):
        if not self._lock.acquire(blocking=False):
            return
        self._released.clear()
        def capture():
            try:
                import sounddevice as sd
                from actions.phone.speech import load_config, make_stt
                frames = []
                with sd.RawInputStream(samplerate=16000, channels=1, dtype="int16") as mic:
                    orb = getattr(ui, "orb", None)
                    if orb:
                        orb.set_activity("local-mic", "listening")
                    try:
                        deadline = time.monotonic() + 20
                        while not self._released.is_set() and time.monotonic() < deadline:
                            data, overflow = mic.read(480)
                            if overflow:
                                raise RuntimeError("Microphone buffer overflow")
                            frames.append(bytes(data))
                    finally:
                        if orb:
                            orb.set_activity("local-mic", None)
                if len(frames) < 8:
                    return
                text = make_stt(load_config())(b"".join(frames), 16000)
                if text:
                    ui._post(lambda: ui.command.setText(text))
                else:
                    ui.write_log("SYS: No clear speech detected.")
            except Exception as exc:
                ui.write_log(f"ERR: Local dictation unavailable ({type(exc).__name__}). Check the microphone and offline speech model.")
            finally:
                self._lock.release()
        threading.Thread(target=capture, daemon=True, name="mark-dictation").start()

    def release(self):
        self._released.set()


def speak_local(text, cancelled):
    """Called on the serialized command worker. Use the Windows default voice."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        speaker.Speak(text[:600], 1 | 16)
        while not speaker.WaitUntilDone(100):
            if cancelled.is_set():
                speaker.Speak("", 1 | 2 | 16)
                break
    finally:
        pythoncom.CoUninitialize()
