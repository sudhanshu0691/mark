# MARK native desktop design system

## Product and interaction model

MARK is a Windows-first assistant used for conversation, visible task execution, and review of evidence. Treat it as a desktop work surface, not a marketing page. Keep the command composer, current assistant state, task progress, and verification easy to find without scrolling the whole window.

Use one resizable dashboard with a conversation area and a right-side task/evidence panel. The right panel can use tabs for Tasks, Verify, Info, and Camera. Keep the camera off until the user explicitly starts it. Keep long output in scrollable, bounded panes. Show completion state in text as well as color.

## Visual tokens

| Token | Value | Use |
| --- | --- | --- |
| Background | `#020617` | Main window |
| Surface | `#0F172A` | Panels |
| Raised surface | `#17263A` | Buttons and controls |
| Border | `#26364D` | Panel and input outlines |
| Text | `#E2E8F0` | Main copy |
| Muted | `#94A3B8` | Supporting labels |
| Primary accent | `#22D3EE` | Focus and selected state |
| Positive | `#86EFAC` | Verified completion |
| Warning | `#FDBA74` | In progress or caution |
| Failure | `#FCA5A5` | Failed action and stop control |

Use Segoe UI for body text, with a mono face only for machine output and code. Apply 8 px control spacing, 14–16 px panel padding, 10–12 px panel radius, and visible keyboard focus. Use short color transitions on hover; do not animate layout or introduce per-frame decoration.

## Components and behavior

- The command field accepts Enter and has a clearly labeled Send button. Keep it available while a task is running.
- The state chip names the current state (Listening, Working, Speaking, Mic Muted, Offline).
- Stop Task interrupts the active request and uses a distinct danger treatment.
- Task entries show running/completed/failed state and a concise result. Verification details remain separately inspectable.
- Charts and diagrams open in a resizable image viewer with preserved aspect ratio.
- Use scrollable output panes with a bounded history. Do not let status text overflow or cover controls.
- Dialogs must state why a key, camera, or sensitive action is being requested; password fields mask secrets.
- Keep focus indicators visible, preserve normal keyboard navigation, and provide labels independent of color.

## Avoid

- Horizontal-scroll landing-page patterns, decorative particle animation, emoji icons, hidden microphone/camera activation, opaque tool output, and success colors without a written status.
- Layout-shifting hover effects, low contrast labels, unbounded logs, modal prompts for routine low-risk actions, and duplicate controls for the same capability.

## Accessibility and sizing

The dashboard targets a minimum 980 x 660 desktop window and must remain usable when resized. Text should remain readable at 100–150% Windows display scaling. Keep keyboard paths for command entry, send, stop, tabs, and file selection. Check contrast at or above WCAG AA for body text and expose explicit text labels for every state.
