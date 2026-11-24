# Answering calls for you

Off by default. It answers only if **you** armed it with a command, e.g. "main absent hu, Rahul ya 9876543210 ka call aaye to utha lo, message le lo".
Tool: `call_agent` (arm | disarm | status | stop | recent). Callers not named are never answered.

## One-time setup (all free)
1. `pip install pyaudiowpatch faster-whisper` (done by setup).
2. Install **VB-CABLE** (vb-audio.com/Cable, free for personal use).
3. In WhatsApp Desktop / Phone Link / Teams set the **microphone** to `CABLE Output`. Keep the speakers on your normal device (Mark hears the caller through speaker loopback).
4. Copy `config/phone.example.json` to `config/phone.json` and set your name and language (`en` or `hi`).
5. Put names and numbers in `config/contacts.json` so a name can match a number.
Arming fails with a clear list if something above is missing.

## Rules that are enforced in code
- The first words are fixed: Mark is an AI assistant and the call is being recorded. Recording starts only after that.
- If the caller objects to recording, the audio is deleted and the call ends.
- Requests for OTP, PIN, password, card or bank details get a fixed refusal; the model never sees them.
- Limits: max duration, silence timeout, `action=stop` kill-switch. One call at a time.
- Rules expire (default 2 hours, max 12).

Recording laws differ by place (some need every party's consent); the disclosure is not optional.
Saved in `data/calls/<time>_<caller>/`: `caller.wav`, `mark.wav`, `transcript.md`, `summary.md`, `meta.json`.
Honest limits: expect 1-3 seconds delay per reply; Windows popups must expose their Answer button (keep notifications on, no Do-Not-Disturb). This path could not be tested with a real phone call here - only the logic, with fakes. Test it with a friend before relying on it.
