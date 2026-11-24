# Local identity monitoring

The PySide6 dashboard has an opt-in camera monitor. Use the **CAMERA** control
to start or pause it. The monitor is never started during application
construction. It captures camera frames with OpenCV, detects faces locally,
matches only against `memory/identity/`, and reports camera state beside the
preview.

## Configuration

Edit [runtime.env](../config/runtime.env) and load it using the project's
normal environment loading mechanism. `MARK_WHATSAPP_RECIPIENT` must be a
complete international number beginning with `+`; the configured request
number is intentionally not rewritten because its country code was not
provided. The Meta provider also requires a WhatsApp Business sender ID and
access token.

OpenCV and requests are already listed in `requirements.txt`. Camera access
must be allowed by Windows privacy settings. Provider delivery cannot be
verified without valid Meta credentials and an eligible WhatsApp Business
account.

## Identity data

Identity metadata and embeddings are stored locally below
`memory/identity/<name>/face_data.json`. The directory is ignored by git.
`IdentityManager.register(name, faces)` is an explicit registration API; no
unknown face is registered automatically. A caller must capture and validate
the intended person's face before invoking it. The lightweight embedding is
for recognition convenience, not authentication.

## Alert policy

An alert is eligible only when exactly one reliable face is visible and that
face is unmatched for the configured duration (10 seconds by default).
Known-plus-unknown, multiple unknown faces, unreliable frames, camera
disconnects, and any person change reset the timer. A cooldown prevents
repeated alerts. The Meta API must confirm acceptance before the dashboard
reports success; snapshots are uploaded through the provider's media endpoint
and are not published as public URLs.

The current UI integration provides monitoring and alert delivery controls.
Voice-driven guided enrollment and liveness/presentation-attack detection
remain separate follow-up work and must not be treated as authentication.
