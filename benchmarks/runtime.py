"""Offline repeatable microbenchmarks; no cloud requests or hardware required."""
import json
import statistics
import time
from core.cloud_privacy import redact_messages
from core.agents.specialist import SpecialistExecutor
from core.voice_stream import SentenceBuffer

def measure(fn, count=1000):
    samples = []
    for _ in range(count):
        start = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - start) / 1000)
    return {"iterations": count, "median_us": statistics.median(samples), "p95_us": sorted(samples)[int(count * .95)-1]}

def main():
    agent = SpecialistExecutor([{"name":"calculate","parameters":{"properties":{}}}], lambda *_: "[DONE]")
    payload = [{"role":"user","content":"password=example123"}]
    def buffer():
        b = SentenceBuffer()
        b.feed("Hello. This is MARK.")
        b.flush()
    print(json.dumps({"redaction":measure(lambda:redact_messages(payload, {})), "specialist_dispatch":measure(lambda:agent.execute("calculate", {})), "sentence_buffer":measure(buffer)}, indent=2))

if __name__ == "__main__":
    main()
