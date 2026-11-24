# MARK

A Windows-first multi-agent runtime with a native PySide6 dashboard, PC and browser automation, Excel workflows, visualization, notifications, and hot-loaded plugins. The Vision Department adds opt-in Qubo camera monitoring and local inference; see [Vision Department](VISION_DEPARTMENT.md).

## Run on Windows

Install 64-bit Python 3.11 or 3.12 and run the following in PowerShell from the project directory. Setup uses the locked dependency manifest in `tooling/`. MARK starts with the microphone enabled for Gemini Live. Use the microphone button to mute or unmute it. Camera monitoring starts in the background without a live preview panel.

```powershell
.\setup_windows.bat
.\run_mark.bat
```

The Windows distribution also includes `packaging/MARK.exe`. For a console launch or a diagnostic check use the project interpreter.

```powershell
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m tooling.setup --doctor
.\.venv\Scripts\python.exe -m core.llm check
```

## Models and voice

Typed requests use automatic Groq routing with Gemini fallback. The configured routes use Qwen3.8 27B for conversation, fast responses, tool selection and memory, GPT-OSS 120B for planning and coding, and Gemini 3.1 Flash-Lite for vision and fallback. Each provider applies its own account limits. Model availability and eligibility depend on your provider account.

Type **mark status** for runtime diagnostics. The `offline` profile runs supported local commands and disables cloud inference. Install optional local speech separately if needed.

```powershell
.\.venv\Scripts\python.exe -m tooling.setup --offline-speech
$env:MARK_PROFILE = 'offline'
.\.venv\Scripts\python.exe main.py
```

Gemini Live requires a Gemini credential and network access. SQLite keeps typed and voice transcripts, including interrupted turns. Enable `share_history_with_cloud` in `config/llm.json` to share completed conversation history with cloud models. The **Clear saved conversation history** menu action deletes local saved turns.

`cloud_limits.requests_per_task` bounds provider requests during one task. Set `requests_per_day` to zero to rely on provider daily quotas. Desktop and camera cloud analysis requires local confirmation.

### LiveKit desktop sharing

Set `LIVEKIT_REMOTE_IDENTITY` to the authorized remote identity and enable `MARK_LIVEKIT_DESKTOP_VIDEO=1` on the desktop. In a connected LiveKit session, request **start desktop video 1080p monitor 1** and approve the dialog on the desktop. Use **720p** or another connected monitor number as needed. The header shows **Stop sharing** during the share. Disconnecting ends the share and a new session requires fresh consent. The optional `MARK_LIVEKIT_DESKTOP_PREVIEW=1` enables the lightweight **start desktop preview** command.

On Windows, request **start window video 1080p Exact window title** to share one visible window. The consent dialog shows the selected title. Sharing ends if the window closes, is minimized, or changes its title.

### Recover a workflow

MARK logs a recovery ID after securely saving a delegated workflow. Use **list recovered workflows** and **resume workflow ID** to continue after interruption. Completed steps are kept. An action interrupted before verification is treated as uncertain, and its dependent steps stay blocked.

### Trusted plugins

MARK verifies bundled plugin fingerprints before importing Python code. Review external plugins before granting them native access. Record approved relative plugin paths and lowercase SHA-256 digests in local `config/trusted_plugins.json`. The digest uses UTF-8 source bytes with CRLF converted to LF. Removing the entry revokes local approval. For isolated Windows plugins, use local `config/sandbox_plugins.json`. Each relative source path maps to an object with `sha256`, a single `tool` declaration, and an optional `timeout` in seconds. The AppContainer can read the staged Python runtime and request, and returns data through a bounded pipe. No host tools, file passthrough, or network capabilities are granted. Removing approval cancels running sandbox work and blocks future execution. MARK blocks untrusted execution if Windows cannot enforce isolation.

## What is in here
| Folder / file | What it does |
| --- | --- |
| `main.py`, `desktop/ui.py` | assistant runtime and native PySide6 dashboard |
| `core/llm.py` | text and tool-call routing for Groq and Gemini |
| `core/brain.py`, `core/verify.py` | plan -> act -> **verify** loop; the verifier is enforced in code |
| `actions/` | user-facing actions; `actions/phone/` contains the call-answering subsystem |
| `actions/viz/` | internal visualization library for project analysis, UML / C4 / ERD / ISO 5807 diagrams, sequences and DXF drawings |
| `plugins/` | hot-loaded extensions, including the canonical Excel and visualization capabilities |
| `plugins/excel/` | workbook inspection, editing, analysis, summaries, charts, and recalculation |
| `assets/` | UI image and other static resources |
| `memory/`, `config/` | memory code and settings; see [config/README.md](../config/README.md) |
| Voice paths | Gemini Live conversation is handled by `main.py`; phone-call STT/TTS lives in `actions/phone/` |
| `data/` | runtime database and generated output: `proof/`, `visualisations/`, `calls/` (git-ignored) |
| `tooling/` | setup, diagnostics and locked dependencies |
| `tests/` | `python -m tooling.run_tests` |
| `docs/` | LOCAL_AI, VISUALISATION, CALLS, STATUS |

Never commit private config such as `config/api_keys.json`, `config/contacts.json`, `config/permissions.json`, `memory/` or runtime `data/`.

See the [prior audit](REAL_AUDIT_REPORT.md) for historical engineering context.

Presentation preparation content is in [MARK_PPT_COMPLETE_DETAILS.md](MARK_PPT_COMPLETE_DETAILS.md).

## Local camera configuration

To customize the vision camera, copy `vision_department/config/model_config.example.yaml` to `vision_department/config/model_config.yaml` and configure your device locally. The local configuration and enrolled face images are excluded from Git.

## Optional gesture mouse

Install the optional gesture stack with `.venv\Scripts\python.exe -m pip install -r tooling/requirements-gesture.txt` after normal setup. MediaPipe Hands requires NumPy 1.x, protobuf 4.x, and OpenCV below 4.12; this optional install changes the versions in the project environment and must not be combined with the default Windows constraints file.

Gesture mouse and background camera monitoring start automatically when dependencies and the webcam are available. Say ?enable gesture mouse? or ?disable gesture mouse?; the header shows its state. An index finger moves the pointer; pinch thumb and index for a left click or thumb and middle for a right click. Raise index and middle and move them vertically to scroll. Say ?enable drag mode? before holding a left pinch to drag; release to drop. An open palm pauses control. A closed fist pauses mouse actions immediately; hold it for 0.8 seconds to stop gesture control. Camera loss disables gesture control. Gesture processing and camera monitoring are independent and share the webcam capture. Optional tuning values are documented in `config/runtime.example.env`.
