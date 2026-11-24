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
