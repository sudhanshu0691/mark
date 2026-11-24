"""Backward-compatible entry point for MARK environment diagnostics."""
from tooling.setup import doctor

if __name__ == "__main__":
    raise SystemExit(doctor())
