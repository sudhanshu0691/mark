# Remaining acceptance work

The source implementation is consolidated in `docs/STATUS.md`. The following require the target Windows machine, physical devices, or third-party credentials and cannot be proven from this workspace:

- Install and launch the PySide6 dashboard on the target Windows 10/11 display and check scaling, screen-reader behavior, and long-running task responsiveness.
- Exercise wake word, Hindi-English speech recognition, barge-in, echo cancellation, and TTS with the actual microphone and speakers.
- Validate identity/liveness with a camera and a separately evaluated anti-spoof model before treating it as security authentication.
- Run browser workflows on an approved test site; check recovery, user confirmation, and readback.
- Recalculate and inspect formulas in Excel or LibreOffice. The Python workbook library does not calculate formula results.
- Open generated STEP files in a real CAD kernel/viewer when STEP export is configured.
- Validate messaging delivery receipts using a sandbox account and configured webhook.
- Run the end-to-end supervisor, execution, cancellation, checkpoint, and recovery workflow on Windows.

Do not report any of these as passed until the corresponding evidence is recorded.
