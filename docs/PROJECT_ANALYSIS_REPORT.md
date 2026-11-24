# MARK Project Analysis Report

**Review date:** 23 November 2026  
**Scope:** Windows desktop runtime, AI routing, tool execution, plugins, persistence, vision, and automated tests in the supplied workspace.

## Executive summary


## Architecture

| Area | Implementation |
| --- | --- |
| Desktop runtime | `main.py` coordinates typed and voice entry points, dispatch, notifications, and background work. `ui.py` implements the native dashboard. [Runtime](../main.py) · [UI](../ui.py) |
| Planning and inference | `core/llm.py` routes model calls. `core/brain.py` executes model-selected tools with bounded steps and retry limits. `core/verify.py` checks selected effects and can downgrade completion claims. [Model router](../core/llm.py) · [Planner](../core/brain.py) · [Verification](../core/verify.py) |
| Tools and plugins | The application dispatches built-in actions and loads Python plugins in-process. Excel and visualization are implemented as plugins. [Dispatcher](../main.py#L1040) · [Plugin loader](../core/plugins.py#L141) · [Excel](../plugins/excel/plugin.py) · [Visualization](../plugins/visualization.py) |
| Local state | SQLite stores assistant memory and vision events. Task records and generated artifacts use the runtime data directory. [Memory database](../memory/db.py) · [Task manager](../core/task_manager.py) · [Vision event store](../vision_department/events/event_store.py) |
| Vision | The optional Vision Department groups camera integration, motion and human detection, an event store, a dashboard, and local API routes. [Vision manager](../vision_department/manager.py) · [Local API](../vision_department/api/routes.py) · [Vision setup](VISION_DEPARTMENT.md) |

## Findings and priorities

| Priority | Finding | Recommended change |
| --- | --- | --- |
| High | The shared dispatcher starts tool execution without a central user-confirmation check. The risk module has a separate confirmation classifier, and some actions have their own checks. For example, the system settings handler accepts a confirmation value supplied in the tool arguments. This leaves approval enforcement dependent on the entry point and action. [Dispatcher](../main.py#L1040) · [Risk policy](../core/risk.py) · [Settings handler](../actions/computer_settings.py#L1044) | Require a user approval event at the shared dispatcher for destructive actions and external communications. Bind approval to the exact operation and verify the behavior across typed, voice, and plugin entry points. |
| High | The desktop action can execute model-generated Python in the desktop process. Plugins also execute Python in-process when loaded. Both capabilities run in the application’s trust boundary. [Desktop action](../actions/desktop.py#L86) · [Plugin loader](../core/plugins.py#L141) | Replace generated scripts with typed desktop operations. Load explicitly trusted plugins and isolate extensions that need fewer privileges than the desktop process. |
| Medium | Most package requirements are unpinned or broadly constrained. The setup guide targets Python 3.11 and 3.12. The supplied virtual environment uses Python 3.11.0, while the shell’s default Python reports 3.15.2. [Core requirements](../requirements.txt) · [Vision requirements](../requirements-vision.txt) · [Setup guide](../SETUP.md) | Record a tested dependency set, document the test runner dependencies, and validate fresh Windows installations on the supported Python versions. |
| Medium | Verification covers selected tool effects. The desktop, browser, camera, voice, and workbook features also depend on Windows hardware, installed apps, or external accounts. A passing automated suite cannot establish those integrations are ready on a target machine. [Planner verification](../core/brain.py#L147) · [Implementation status](STATUS.md) | Maintain an acceptance checklist for desktop startup and display scaling, model latency, browser actions, Excel recalculation, voice devices, and opt-in camera operation. |

## Validation

All **77 tests passed in 32.03 seconds** using the supplied Python 3.11.0 virtual environment. The workspace contains 17 pytest modules covering planner verification, model routing, memory, calls, visualization, and vision components. The default pytest temporary directory and the existing workspace cache directory returned Windows access errors. A fresh temporary directory directly under the workspace resolved the fixture setup errors.

```powershell
$markTestTemp = Join-Path (Get-Location) ('.mark-test-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --tb=short --basetemp $markTestTemp
```

Live Windows desktop workflows, physical cameras and microphones, provider accounts, and external workbook recalculation were outside the executed checks. The workspace had no Git metadata available to associate this report with a commit.
