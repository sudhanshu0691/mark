"""Local face identity storage and conservative matching.

This module intentionally keeps all identity data under ``memory/identity``.
The embedding used here is a lightweight local feature vector, not an
authentication mechanism. Applications requiring authorization must add a
separate factor.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - exercised by installation checks
    cv2 = None


@dataclass(frozen=True)
class Match:
    name: str | None
    confidence: float
    reliable: bool


class IdentityManager:
    def __init__(self, root: Path | None = None, threshold: float = 0.78):
        self.root = root or Path(__file__).resolve().parent.parent / "memory" / "identity"
        self.threshold = float(threshold)
        self._embeddings: dict[str, list[np.ndarray]] = {}
        self._lock = threading.RLock()
        self.reload()

    def reload(self) -> None:
        loaded: dict[str, list[np.ndarray]] = {}
        if self.root.exists():
            for directory in self.root.iterdir():
                values: list[np.ndarray] = []
                if directory.is_dir():
                    metadata = directory / "face_data.json"
                    try:
                        data = json.loads(metadata.read_text(encoding="utf-8"))
                        values.extend(np.asarray(v, dtype=np.float32) for v in data.get("embeddings", []))
                    except (OSError, ValueError, TypeError, json.JSONDecodeError):
                        pass
                    name = directory.name
                elif directory.is_file() and directory.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
                    image = cv2.imread(str(directory)) if cv2 is not None else None
                    if image is None:
                        continue
                    values.extend(self._photo_faces(image))
                    name = directory.stem
                else:
                    continue
                if values:
                    loaded[name] = values
        with self._lock:
            self._embeddings = loaded

    @staticmethod
    def _photo_faces(image: np.ndarray) -> list[np.ndarray]:
        if cv2 is None:
            return [image]
        cascade_path = getattr(getattr(cv2, "data", None), "haarcascades", "")
        if not cascade_path:
            return [image]
        detector = cv2.CascadeClassifier(cascade_path + "haarcascade_frontalface_default.xml")
        if detector.empty():
            return [image]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        boxes = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        return [image[y:y + h, x:x + w] for x, y, w, h in boxes] or [image]

    @staticmethod
    def _safe_name(name: str) -> str:
        cleaned = re.sub(r"[^\w -]", "", name, flags=re.UNICODE).strip()
        cleaned = re.sub(r"\s+", "_", cleaned)
        if not cleaned:
            raise ValueError("identity name must contain letters or numbers")
        return cleaned[:80]

    @staticmethod
    def embedding(face: np.ndarray) -> np.ndarray:
        if cv2 is None:
            raise RuntimeError("opencv-python is required for local face embeddings")
        gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY) if face.ndim == 3 else face
        resized = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
        vector = resized.reshape(-1)
        vector -= vector.mean()
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector

    def match(self, embedding: np.ndarray) -> Match:
        with self._lock:
            candidates = [
                (name, float(np.dot(embedding, ref)))
                for name, refs in self._embeddings.items()
                for ref in refs
                if ref.shape == embedding.shape
            ]
        if not candidates:
            return Match(None, 0.0, False)
        name, score = max(candidates, key=lambda item: item[1])
        confidence = max(0.0, min(1.0, (score + 1.0) / 2.0))
        return Match(name if confidence >= self.threshold else None, confidence, confidence >= self.threshold)

    def register(self, name: str, faces: Iterable[np.ndarray]) -> Path:
        safe_name = self._safe_name(name)
        embeddings = [self.embedding(face).tolist() for face in faces]
        if not embeddings:
            raise ValueError("at least one usable face image is required")
        directory = self.root / safe_name
        directory.mkdir(parents=True, exist_ok=True)
        metadata = directory / "face_data.json"
        metadata.write_text(json.dumps({"version": 1, "embeddings": embeddings}), encoding="utf-8")
        self._update_index()
        self.reload()
        return directory

    def _update_index(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        index = {"version": 1, "identities": sorted(p.name for p in self.root.iterdir() if p.is_dir())}
        (self.root / "identity_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
