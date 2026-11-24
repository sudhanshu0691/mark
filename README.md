# MARK

A Windows-first multi-agent runtime with a native PySide6 dashboard, PC and browser automation, Excel workflows, visualization, notifications, and hot-loaded plugins. The Vision Department adds opt-in Qubo camera monitoring and local inference; see [docs/VISION_DEPARTMENT.md](docs/VISION_DEPARTMENT.md).

## Run on Windows

Install 64-bit Python 3.11 or 3.12 and run the following in PowerShell from the project directory. Setup uses the Windows Python 3.11 constraints when applicable. MARK starts with the microphone enabled for Gemini Live. Use the microphone button to mute or unmute it. Camera monitoring starts in the background without a live preview panel.

```powershell
.\setup_windows.bat
.\run_mark.bat
```

For a console launch or a diagnostic check use the project interpreter.

```powershell
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe setup.py --doctor
.\.venv\Scripts\python.exe -m core.llm check
```

## Models and voice

Ollama handles typed requests locally. Install Ollama, then run `.venv\Scripts\python.exe -m core.llm pull` to download the configured models. Text and coding use `qwen3:4b`, while image questions use `qwen2.5vl:3b`. Gemini Live handles optional microphone voice and requires a Gemini key and network access.

Type **mark status** for runtime diagnostics. The `offline` profile uses local tools and Ollama, and disables Gemini cloud inference. Local speech can be installed separately.

```powershell
.\.venv\Scripts\python.exe setup.py --offline-speech
$env:MARK_PROFILE = 'offline'
.\.venv\Scripts\python.exe main.py
```

Gemini Live keeps the latest session handle in memory and restores recent voice dialogue from SQLite when a fresh session is needed. Typed follow-ups use the same database. `share_history_with_cloud` opts into sharing stored typed history and profile context. Clear saved turns from **Panels & layout → Clear saved conversation history**. Close the app to end its current Live session.

Cloud text requests support `stream_text`. Set `cloud_limits.requests_per_day` to an application limit per provider for text requests. Zero leaves the daily limit disabled. The planner also uses `requests_per_task`. These limits apply separately from provider billing and Gemini Live audio sessions.

## What is in here
| Folder / file | What it does |
| --- | --- |
| `main.py`, `ui.py` | assistant runtime and native PySide6 dashboard |
| `core/llm.py` | text and tool-call routing for Ollama and Gemini |
| `core/brain.py`, `core/verify.py` | plan -> act -> **verify** loop; the verifier is enforced in code |
| `actions/` | user-facing actions; `actions/phone/` contains the call-answering subsystem |
| `actions/viz/` | internal visualization library for project analysis, UML / C4 / ERD / ISO 5807 diagrams, sequences and DXF drawings |
| `plugins/` | hot-loaded extensions, including the canonical Excel and visualization capabilities |
| `plugins/excel/` | workbook inspection, editing, analysis, summaries, charts, and recalculation |
| `assets/` | UI image and other static resources |
| `memory/`, `config/` | memory code and settings; see [config/README.md](config/README.md) |
| Voice paths | Gemini Live conversation is handled by `main.py`; phone-call STT/TTS lives in `actions/phone/` |
| `data/` | runtime database and generated output: `proof/`, `visualisations/`, `calls/` (git-ignored) |
| `tests/` | `python -m pytest tests` |
| `docs/` | LOCAL_AI, VISUALISATION, CALLS, STATUS |

Never commit private config such as `config/api_keys.json`, `config/contacts.json`, `config/permissions.json`, `memory/` or runtime `data/`.

Engineering evidence is in the [audit](docs/REAL_AUDIT_REPORT.md), [architecture](docs/ARCHITECTURE.md), [performance measurements](docs/PERFORMANCE_REPORT.md), [test results](docs/TEST_REPORT.md), [dependency inventory](docs/DEPENDENCY_AUDIT.md), [security review](docs/SECURITY_REVIEW.md), and [change log](docs/CHANGELOG_MARK.md).

Presentation preparation content is in [MARK_PPT_COMPLETE_DETAILS.md](docs/MARK_PPT_COMPLETE_DETAILS.md).

## Local camera configuration

To customize the vision camera, copy `vision_department/config/model_config.example.yaml` to `vision_department/config/model_config.yaml` and configure your device locally. The local configuration and enrolled face images are excluded from Git.

## Optional gesture mouse

Install the optional gesture stack with `.venv\Scripts\python.exe -m pip install -r requirements-gesture.txt` after normal setup. MediaPipe Hands requires NumPy 1.x, protobuf 4.x, and OpenCV below 4.12; this optional install changes the versions in the project environment and must not be combined with the default Windows constraints file.

Gesture mouse and background camera monitoring start automatically when dependencies and the webcam are available. Say ?enable gesture mouse? or ?disable gesture mouse?; the header shows its state. An index finger moves the pointer; pinch thumb and index for a left click or thumb and middle for a right click. Raise index and middle and move them vertically to scroll. Say ?enable drag mode? before holding a left pinch to drag; release to drop. An open palm pauses control. A closed fist pauses mouse actions immediately; hold it for 0.8 seconds to stop gesture control. Camera loss disables gesture control. Gesture processing and camera monitoring are independent and share the webcam capture. Optional tuning values are documented in `config/runtime.example.env`.
