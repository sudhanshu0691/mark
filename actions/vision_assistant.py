"""Visual Intelligence for MARK.

Provides document understanding, image analysis, and screen-based task assistance.
Routes vision through the configured provider.
No custom model training - uses pretrained vision LLMs.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Optional

from core import llm


VISION_SYSTEM = (
    "You are MARK's visual assistant. Analyze the image/document carefully. "
    "Answer precisely about what you see. Quote visible text exactly. "
    "If something is unclear or not visible, say so - never guess. "
    "Reply concisely in the user's language (Hinglish is fine)."
)


def analyze_image(image_bytes: bytes, question: str, use_cloud: bool = False) -> str:
    """Analyze an image with the local vision model, falling back to Gemini."""
    from core.llm import types as gt

    text, images = llm._normalise([gt.Part.from_bytes(image_bytes, "image/jpeg"), question])
    role = "vision"

    # Try local vision first
    try:
        return llm.generate([image_bytes, question], role=role,
                            system=VISION_SYSTEM, temperature=0.1)
    except Exception as e:
        print(f"[Vision] Local vision failed: {e}; trying Gemini fallback...")

    # Cloud fallback
    if use_cloud:
        try:
            return llm._gemini_generate(role, [image_bytes, question], None)
        except Exception as e2:
            return f"[Vision] Both local and cloud vision failed: {e2}"

    return f"[Vision] Local vision unavailable: {e}"


def analyze_document(file_path: str | Path, question: str) -> str:
    """Analyze a document (PDF, image, text, code) and answer a question about it."""
    path = Path(file_path)
    if not path.exists():
        return f"[Vision] File not found: {path}"

    ext = path.suffix.lower()

    # Text-based documents - read directly
    if ext in {".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".csv", ".log", ".ini", ".yaml", ".yml", ".toml"}:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[:30000]
            prompt = (
                f"Document: {path.name}\n"
                f"Question: {question}\n\n"
                f"Content:\n{content}\n\n"
                "Answer based on the document content. Quote relevant parts."
            )
            return llm.generate(prompt, role="text", system=VISION_SYSTEM, temperature=0.2)
        except Exception as e:
            return f"[Vision] Could not read document: {e}"

    # PDF - use file_processor
    if ext == ".pdf":
        from actions.file_processor import file_processor
        return file_processor({"file_path": str(path), "action": "extract_text", "instruction": question})

    # DOCX
    if ext in {".docx", ".doc"}:
        from actions.file_processor import file_processor
        return file_processor({"file_path": str(path), "action": "extract_text", "instruction": question})

    # PPTX
    if ext in {".pptx", ".ppt"}:
        from actions.file_processor import file_processor
        return file_processor({"file_path": str(path), "action": "extract_text", "instruction": question})

    # Images - vision model
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif"}:
        try:
            image_bytes = path.read_bytes()
            return analyze_image(image_bytes, question, use_cloud=True)
        except Exception as e:
            return f"[Vision] Could not analyze image: {e}"

    return f"[Vision] Unsupported document type: {ext}"


def screen_task_assistance(goal: str, current_screen_description: str = "") -> str:
    """Help with screen-based tasks: what to click, where to type, next steps."""
    prompt = (
        f"GOAL: {goal}\n"
        f"CURRENT SCREEN: {current_screen_description or '(unknown - ask user to describe or use screen_read)'}\n\n"
        "Based on the goal and current screen state, tell the user exactly what to do next: "
        "which button to click, what to type, which menu to open. "
        "Be specific and actionable. If you cannot determine the next step without seeing the screen, "
        "ask the user to run screen_read mode=ask with a specific question."
    )
    return llm.generate(prompt, role="text", system=VISION_SYSTEM, temperature=0.2)
