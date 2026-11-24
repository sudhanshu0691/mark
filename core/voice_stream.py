"""Incremental speech segmentation; provider chunks need not align to words."""

import re


class SentenceBuffer:
    def __init__(self, max_chars=240):
        self.pending = ""
        self.max_chars = max_chars

    def feed(self, delta):
        self.pending += delta
        out = []
        while self.pending:
            match = re.search(r"[.!?।](?:\s+|$)|\n+", self.pending)
            if match:
                end = match.end()
            elif len(self.pending) >= self.max_chars:
                end = self.pending.rfind(" ", 0, self.max_chars)
                if end <= 0:
                    end = self.max_chars
            else:
                break
            part, self.pending = self.pending[:end].strip(), self.pending[end:]
            if part:
                out.append(part)
        return out

    def flush(self):
        value, self.pending = self.pending.strip(), ""
        return value


class PlaybackBargeIn:
    """Use recent speaker PCM to suppress direct echo and gate interruptions."""

    def __init__(self, microphone_rate=16_000, speaker_rate=24_000):
        import threading
        from collections import deque

        self.microphone_rate = microphone_rate
        self.speaker_rate = speaker_rate
        self._lock = threading.Lock()
        self._reference = deque(maxlen=16)
        self._candidate = deque(maxlen=5)
        self._voiced = 0
        self._triggered = False

    def playback(self, pcm):
        import time
        import numpy as np

        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        if samples.size == 0:
            return
        positions = np.arange(
            round(samples.size * self.microphone_rate / self.speaker_rate)
        )
        reference = np.interp(
            positions * self.speaker_rate / self.microphone_rate,
            np.arange(samples.size),
            samples,
        ).astype(np.float32)
        now = time.monotonic()
        with self._lock:
            self._reference.append((now, reference))

    def microphone(self, pcm, *, playing):
        import time
        import numpy as np

        now = time.monotonic()
        with self._lock:
            references = [
                samples
                for timestamp, samples in self._reference
                if now - timestamp <= 0.4
            ]
        signal = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        if signal.size == 0:
            return b"", False
        if references:
            reference = np.concatenate(references)
            if reference.size >= signal.size:
                # Coarse offset search, followed by sample-accurate alignment.
                coarse_ref, coarse_signal = reference[::4], signal[::4]
                corr = np.correlate(coarse_ref, coarse_signal, mode="valid")
                sums = np.concatenate(
                    ([0.0], np.cumsum(coarse_ref.astype(np.float64) ** 2))
                )
                energy = sums[coarse_signal.size :] - sums[: -coarse_signal.size]
                offset = int(np.argmax(corr / np.sqrt(np.maximum(energy, 1)))) * 4
                signal_power = float(np.dot(signal, signal))
                best = (0.0, None, 0.0)
                for start in range(
                    max(0, offset - 5),
                    min(reference.size - signal.size, offset + 5) + 1,
                ):
                    sample = reference[start : start + signal.size]
                    power = float(np.dot(sample, sample))
                    product = float(np.dot(signal, sample))
                    similarity = product / max(1.0, (power * signal_power) ** 0.5)
                    if similarity > best[0]:
                        best = similarity, sample, product / max(power, 1)
                if best[0] >= 0.68:
                    signal -= best[1] * best[2]
                    pcm = signal.clip(-32768, 32767).astype("<i2").tobytes()

        if not playing:
            self._candidate.clear()
            self._triggered = False
            self._voiced = 0
            return pcm, False
        if self._triggered:
            return pcm, False
        rms = float(np.sqrt(np.mean(signal * signal)))
        if rms < 850 or not references:
            self._candidate.clear()
            self._voiced = 0
            return b"", False
        self._candidate.append(pcm)
        self._voiced += signal.size
        if self._voiced < 0.12 * self.microphone_rate:
            return b"", False
        self._triggered = True
        output = b"".join(self._candidate)
        self._candidate.clear()
        return output, True
