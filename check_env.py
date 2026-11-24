#!/usr/bin/env python
import sys
import os

# Add the Mark directory to path
sys.path.insert(0, r"e:\Mark")

print("Python executable:", sys.executable)
print("Working directory:", os.getcwd())

# Check livekit packages
packages = ["livekit", "livekit-agents", "livekit-plugins-silero", "faster-whisper", "pyttsx3", "piper-tts"]
for pkg in packages:
    try:
        __import__(pkg)
        print(f"✓ {pkg} is installed")
    except ImportError as e:
        print(f"✗ {pkg} NOT installed: {e}")

# Check local modules
local_modules = ["core", "memory", "actions", "ui"]
for mod in local_modules:
    try:
        __import__(mod)
        print(f"✓ {mod} local module imports OK")
    except Exception as e:
        print(f"✗ {mod} import failed: {e}")

# Check key imports
try:
    import sounddevice
    print(f"✓ sounddevice {sounddevice.__version__} available")
except Exception as e:
    print(f"✗ sounddevice failed: {e}")

try:
    import numpy
    print(f"✓ numpy {numpy.__version__} available")
except Exception as e:
    print(f"✗ numpy failed: {e}")

try:
    import scipy
    print(f"✓ scipy {scipy.__version__} available")
except Exception as e:
    print(f"✗ scipy failed: {e}")

try:
    import cv2
    print(f"✓ opencv-python {cv2.__version__} available")
except Exception as e:
    print(f"✗ opencv-python failed: {e}")

try:
    from piper import PiperVoice
    print("✓ piper-tts available")
except Exception as e:
    print(f"✗ piper-tts failed: {e}")

try:
    from faster_whisper import WhisperModel
    print(f"✓ faster-whisper available")
except Exception as e:
    print(f"✗ faster-whisper failed: {e}")

try:
    import websockets
    print(f"✓ websockets available")
except Exception as e:
    print(f"✗ websockets failed: {e}")

print("\nDone checking environment.")