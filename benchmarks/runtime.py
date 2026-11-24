"""Repeatable runtime microbenchmarks using temporary local state."""

import json
import platform
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.agents.orchestrator import select_route
from core.agents.specialist import SpecialistExecutor
from core.cloud_privacy import redact_messages
from core.voice_stream import SentenceBuffer
from memory import db


def measure(fn, count=1000):
    samples = []
    for _ in range(count):
        start = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - start) / 1000)
    ordered = sorted(samples)
    return {
        "iterations": count,
        "median_us": statistics.median(samples),
        "p95_us": ordered[int(count * 0.95) - 1],
        "p99_us": ordered[int(count * 0.99) - 1],
    }


def sentence_buffer():
    buffer = SentenceBuffer()
    buffer.feed("Hello. This is MARK.")
    buffer.flush()


def stability(work, seconds):
    import psutil

    process = psutil.Process()
    started = time.monotonic()
    initial = process.memory_info().rss
    peak = initial
    iterations = 0
    while time.monotonic() - started < seconds:
        work()
        iterations += 1
        if iterations % 25 == 0:
            peak = max(peak, process.memory_info().rss)
    final = process.memory_info().rss
    return {
        "duration_s": round(time.monotonic() - started, 2),
        "iterations": iterations,
        "baseline_rss_bytes": initial,
        "peak_sampled_rss_bytes": max(peak, final),
        "ending_rss_bytes": final,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--stability-seconds", type=int, default=5)
    options = parser.parse_args()
    if not 0 <= options.stability_seconds <= 3600:
        parser.error("stability duration must be between zero and 3600 seconds")
    payload = [{"role": "user", "content": "password=example123"}]
    with tempfile.TemporaryDirectory(prefix="mark-bench-") as tmp:
        root = Path(tmp)
        db.DB_PATH = root / "memory.db"
        db.LEGACY_LONG_TERM = root / "unused-memory.json"
        db.LEGACY_CONVERSATION = root / "unused-history.json"
        conversation_id = db.get_active_conversation()["id"]

        def write():
            db.append_conversation_turn(
                "Index test phrase", "Example response", conversation_id=conversation_id
            )

        def retrieve():
            return db.search_conversation_turns(
                "test phrase", conversation_id=conversation_id, limit=5
            )

        def dispatch():
            agent = SpecialistExecutor([{"name": "calculate"}], lambda *_: "[DONE]")
            return agent.execute("calculate", {})

        results = {
            "environment": {
                "os": platform.platform(),
                "python": platform.python_version(),
                "sqlite_fts5": db.FTS_ENABLED,
            },
            "redaction": measure(lambda: redact_messages(payload, {})),
            "routing": measure(
                lambda: select_route("Summarize the research and compare the results")
            ),
            "stubbed_tool_dispatch": measure(dispatch),
            "sentence_buffer": measure(sentence_buffer),
            "sqlite_conversation_write": measure(write, 200),
            "sqlite_retrieval": measure(retrieve, 200),
        }
        with ThreadPoolExecutor(max_workers=4) as pool:
            results["four_concurrent_retrievals"] = measure(
                lambda: [
                    job.result() for job in [pool.submit(retrieve) for _ in range(4)]
                ],
                100,
            )

        def mixed():
            write()
            retrieve()
            dispatch()

        if options.stability_seconds:
            results["stability_local_memory_and_dispatch"] = stability(
                mixed, options.stability_seconds
            )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
