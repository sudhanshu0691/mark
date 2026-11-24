# ML privacy and safety

- Local image and audio adapters are disabled by default.
- Images are passed to Gemini only through the existing configured Gemini vision
  path; enabling a local adapter does not silently upload images.
- Speaker profiles contain embeddings, not raw audio. They are stored locally and
  should be deleted when no longer needed.
- A speaker match is advisory and must not be the sole authorization for destructive,
  financial, messaging, or other sensitive actions.
- Unknown or below-threshold speakers are rejected rather than guessed.
- Do not put API keys, recordings, or personal data in the model cache directory.
- Disable local ML with `ml.enabled: false` and feature-specific `enabled` flags.
- Model output is advisory for NLP: it cannot call tools or bypass the existing
  Gemini dispatcher and confirmation rules.
