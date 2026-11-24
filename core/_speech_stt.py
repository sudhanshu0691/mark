"""Speech-to-Text module for MARK using Faster Whisper.

Provides local streaming speech recognition with VAD-based utterance segmentation.
Supports Hindi, English and Hinglish transcription.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Tuple

logger = logging.getLogger(__name__)