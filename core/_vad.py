"""Local PCM speech endpointing with adaptive noise floor and pre-roll."""

from collections import deque

import numpy as np


class VoiceActivityDetector:
    """Accept mono signed PCM and emit one bounded utterance after a pause."""

    def __init__(self, sample_rate=16_000):
        self.bytes_per_second = sample_rate * 2
        self.pre_roll = deque()
        self.pre_roll_size = 0
        self.utterance = bytearray()
        self.noise = 100.0
        self.attack = 0
        self.silence = 0

    def feed(self, frame, *, playback=False):
        """Return whether speech just started and any completed utterance."""
        if not frame or len(frame) % 2 or len(frame) > self.bytes_per_second // 2:
            return False, None
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32)
        rms = float(np.sqrt(np.mean(samples * samples)))
        voiced = rms >= max(
            850 if playback else 350, self.noise * (5 if playback else 3)
        )
        if not self.utterance and not voiced and not playback:
            self.noise = min(1800.0, self.noise * 0.92 + rms * 0.08)

        if not self.utterance:
            self.pre_roll.append(frame)
            self.pre_roll_size += len(frame)
            while self.pre_roll_size > self.bytes_per_second // 5:
                self.pre_roll_size -= len(self.pre_roll.popleft())
            self.attack = self.attack + len(frame) if voiced else 0
            if self.attack < self.bytes_per_second * (0.12 if playback else 0.06):
                return False, None
            self.utterance.extend(b"".join(self.pre_roll) or frame)
            self.pre_roll.clear()
            self.pre_roll_size = 0
            return True, None

        self.utterance.extend(frame)
        self.silence = 0 if voiced else self.silence + len(frame)
        if (
            self.silence < self.bytes_per_second * 0.55
            and len(self.utterance) < self.bytes_per_second * 10
        ):
            return False, None
        trailing = max(0, self.silence - self.bytes_per_second // 10)
        voiced_length = len(self.utterance) - self.silence
        packet = bytes(self.utterance[: len(self.utterance) - trailing])
        self.utterance.clear()
        self.silence = self.attack = 0
        return False, packet if voiced_length >= self.bytes_per_second * 0.12 else None
