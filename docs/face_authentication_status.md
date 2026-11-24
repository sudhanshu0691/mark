# Face Authentication Status

## Current status

The opt-in local camera monitoring and unknown-person alert pipeline is
implemented separately from authentication. It does not unlock Mark or
authorize sensitive actions.

The current application continues to use its existing startup flow, UI, camera
vision tools, Gemini configuration, memory, plugins, and command dispatcher.
Face detection must not be treated as authentication until the phases below
are implemented and tested together.

## Not implemented

- First-run secure setup and API validation
- OS credential-manager storage for the Gemini API key
- Guided identity enrollment and camera-session management
- Guided realtime multi-angle enrollment
- Persistent user profile and biometric embedding storage
- Identity matching on every startup
- Independent liveness and presentation-attack checks
- Unknown-person and multiple-face rejection
- Central authorization enforcement for protected tools
- Logout, inactivity lock, and restart re-authentication
- Security-event audit logging
- Automated tests using mocked camera frames and recognition outcomes

## Planned implementation phases

1. **Design and interfaces** — define camera, profile, embedding, liveness,
   authorization, and audit-log contracts without changing startup behavior.
2. **Credential and profile storage** — add secure storage, migration,
   permissions, profile integrity protection, and explicit error handling.
3. **Realtime camera layer** — the current monitoring layer provides a
   continuously running preview, disconnect reporting, and bounded frame work.
4. **Enrollment** — guide the user through multiple poses and lighting
   conditions, collect only valid frames, and save a profile only after a
   complete verified transaction.
5. **Authentication and liveness** — require one eligible face, independent
   liveness checks, configurable thresholds, retry limits, and cooldowns.
6. **Authorization integration** — enforce the locked state in the central
   command dispatcher, not only in the UI.
7. **Monitoring and session controls** — add opt-in continuous monitoring,
   grace periods, logout, inactivity locking, and re-authentication.
8. **Testing and documentation** — test setup, camera failures, identity
   mismatch, unknown/multiple faces, liveness outcomes, cooldowns, protected
   commands, and regression behavior.

## Security and privacy constraints

- Do not store raw face images by default.
- Do not log API keys, passwords, raw frames, or unnecessary biometric data.
- Do not automatically register unknown faces.
- Do not upload strangers' images.
- Do not claim that a conventional webcam provides complete anti-spoofing
  protection; document the limitations of the selected model and checks.
- A failed camera or recognition operation must keep protected features locked,
  never silently bypass authentication.
