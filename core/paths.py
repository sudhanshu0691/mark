"""Canonical paths for generated output and local runtime data."""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PROOF_DIR = DATA_DIR / "proof"            # before/after screenshots used by the verifier
VIZ_DIR = DATA_DIR / "visualisations"     # diagrams and drawings
CALLS_DIR = DATA_DIR / "calls"            # call recordings, transcripts, summaries


def ensure(*dirs: Path) -> None:
    for d in dirs or (DATA_DIR, PROOF_DIR, VIZ_DIR, CALLS_DIR):
        d.mkdir(parents=True, exist_ok=True)
