"""
Media Studio - analyse and edit images / audio with Pillow, OpenCV and pydub.

Analysis reports only what it really measured (dimensions, duration, codec...).
Editing always reopens the output file and reports its real size/duration before
saying DONE, so a failed export can never look like a success.
"""
from __future__ import annotations

import os
from pathlib import Path

from core.result import done, failed, unverified

NAME = "media_studio"
DESCRIPTION = ("Multimedia analysis and editing: inspect image/video/audio properties, "
               "resize/crop/convert/compress images, trim/volume/convert audio. Verifies outputs.")
METADATA = {"version": "1.0.0",
            "capabilities": ["image", "audio", "video", "media", "photo", "editing", "convert"],
            "permissions": [], "timeout": 90}

DECLARATION = {
    "name": "media_studio",
    "description": ("Analyse or edit a media file. "
                    "action: info | convert | resize | crop | rotate | compress | thumbnail | "
                    "audio_info | audio_convert | audio_trim | audio_volume | video_info"),
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "description": "info | convert | resize | crop | rotate | compress | thumbnail | audio_info | audio_convert | audio_trim | audio_volume | video_info"},
        "path": {"type": "STRING", "description": "Input file"},
        "out": {"type": "STRING", "description": "Output file (defaults to a new name)"},
        "width": {"type": "INTEGER", "description": "Target width (resize)"},
        "height": {"type": "INTEGER", "description": "Target height (resize)"},
        "percent": {"type": "INTEGER", "description": "Scale percent (resize)"},
        "format": {"type": "STRING", "description": "Target format: png/jpg/webp/mp3/wav/..."},
        "quality": {"type": "INTEGER", "description": "1-100 quality for compress/convert"},
        "start": {"type": "INTEGER", "description": "audio trim start seconds"},
        "duration": {"type": "INTEGER", "description": "audio trim duration seconds"},
        "gain": {"type": "NUMBER", "description": "audio volume multiplier (0.5 = half, 2 = double)"},
        "angle": {"type": "INTEGER", "description": "rotate degrees (90/180/270)"},
    }, "required": ["action", "path"]},
}

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
AUD_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma"}
VID_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv"}


def _p(x) -> Path:
    return Path(str(x or "")).expanduser()


def _kind(p: Path) -> str:
    e = p.suffix.lower()
    if e in IMG_EXTS: return "image"
    if e in AUD_EXTS: return "audio"
    if e in VID_EXTS: return "video"
    return "unknown"


def _must(p: Path) -> str | None:
    if not p.name:
        return "no path given"
    if not p.exists():
        return f"file not found: {p}"
    if not p.is_file():
        return f"not a file: {p}"
    return None


def _out(args, src: Path, ext: str) -> Path:
    o = _p(args.get("out"))
    if not o.name:
        o = src.with_name(f"{src.stem}_mark{ext}")
    if o.suffix.lower() != ext:
        o = o.with_suffix(ext)
    return o


def _verify_out(p: Path) -> str | None:
    """Reopen the written file - returns an error string, or None when it is real."""
    if not p.exists():
        return "output file was not written"
    if p.stat().st_size < 64:
        return f"output is {p.stat().st_size} bytes - too small to be valid"
    e = p.suffix.lower()
    try:
        if e in IMG_EXTS:
            from PIL import Image
            with Image.open(p) as im:
                im.verify()
            with Image.open(p) as im:
                w, h = im.size
                if w < 1 or h < 1:
                    return f"image reports {w}x{h}"
        elif e in AUD_EXTS:
            from pydub import AudioSegment
            seg = AudioSegment.from_file(str(p))
            if len(seg) <= 0:
                return "audio file has zero duration"
        elif e in VID_EXTS:
            import cv2
            cap = cv2.VideoCapture(str(p))
            if not cap.isOpened():
                return "video decoder could not open the output"
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            cap.release()
            if n <= 0:
                return "video reports 0 frames"
    except Exception as ex:
        return f"output cannot be reopened: {type(ex).__name__}: {ex}"
    return None


def run(args: dict, ctx):
    act = str(args.get("action", "info")).lower().strip()
    src = _p(args.get("path"))
    err = _must(src)
    if err and act != "convert":
        return failed(f"Cannot process {src.name or '(no path)'}", err)

    # ── image info ────────────────────────────────────────────────────────────
    if act in ("info", "image_info"):
        if _kind(src) == "image":
            from PIL import Image
            with Image.open(src) as im:
                w, h = im.size
                mode, fmt = im.mode, (im.format or "?")
                frames = getattr(im, "n_frames", 1)
                exif = {}
                try:
                    exif = {k: str(v)[:60] for k, v in list((im.getexif() or {}).items())[:6]}
                except Exception:
                    pass
            kb = src.stat().st_size / 1024
            return done(f"{src.name}: {w}x{h} {mode} {fmt}" + (f" ({frames} frames)" if frames > 1 else ""),
                        evidence=f"size={kb:.1f} KB, mode={mode}, format={fmt}, animated={frames > 1}, exif={exif}")
        if _kind(src) == "audio":
            return run({"action": "audio_info", "path": str(src)}, ctx)
        if _kind(src) == "video":
            return run({"action": "video_info", "path": str(src)}, ctx)
        return failed(f"Unknown media type: {src.suffix}", "not an image, audio or video file")

    # ── image editing ─────────────────────────────────────────────────────────
    if act in ("convert", "resize", "crop", "rotate", "compress", "thumbnail", "grayscale", "flip"):
        if _kind(src) != "image":
            return failed(f"{src.name} is not an image",
                          f"extension {src.suffix} is not one of {sorted(IMG_EXTS)}")
        from PIL import Image
        fmt = str(args.get("format") or "").lower().strip().lstrip(".")
        ext_map = {"jpg": ".jpg", "jpeg": ".jpg", "png": ".png", "webp": ".webp", "bmp": ".bmp", "gif": ".gif"}
        if not fmt:
            ext_map_default = {".jpg": ".jpg", ".jpeg": ".jpg", ".png": ".png", ".webp": ".webp"}
            if act in ("convert",):
                return failed("No target format given", "format is required, e.g. format=png")
            ext = ext_map_default.get(src.suffix.lower(), ".png")
        else:
            if fmt not in ext_map:
                return failed(f"Unsupported target format '{fmt}'", f"use one of {sorted(ext_map)}")
            ext = ext_map[fmt]
        out = _out(args, src, ext)
        q = int(args.get("quality") or 90)
        q = max(1, min(q, 100))
        try:
            with Image.open(src) as im:
                im.load()
                w0, h0 = im.size
                if act == "resize":
                    if args.get("percent"):
                        pc = max(1, min(int(args["percent"]), 1000)) / 100.0
                        nw, nh = int(w0 * pc), int(h0 * pc)
                    elif args.get("width") and args.get("height"):
                        nw, nh = int(args["width"]), int(args["height"])
                    elif args.get("width"):
                        nw = int(args["width"])
                        nh = max(1, int(nw * h0 / w0))
                    elif args.get("height"):
                        nh = int(args["height"])
                        nw = max(1, int(nh * w0 / h0))
                    else:
                        return failed("Nothing to resize", "give width, height or percent")
                    if nw < 1 or nh < 1:
                        return failed("Invalid target size", f"computed {nw}x{nh}")
                    im = im.resize((nw, nh), Image.LANCZOS)
                elif act == "crop":
                    fw = max(0.01, min(float(args.get("width") or 1.0), 1.0))
                    fh = max(0.01, min(float(args.get("height") or 1.0), 1.0))
                    left, top = int(w0 * (1 - fw) / 2), int(h0 * (1 - fh) / 2)
                    im = im.crop((left, top, left + int(w0 * fw), top + int(fh * h0)))
                elif act == "rotate":
                    ang = int(args.get("angle") or 90)
                    im = im.rotate(ang, expand=True)
                elif act == "flip":
                    im = im.transpose(Image.FLIP_TOP_BOTTOM)
                elif act == "grayscale":
                    im = im.convert("L")
                elif act == "thumbnail":
                    im.thumbnail((int(args.get("width") or 512), int(args.get("height") or 512)), Image.LANCZOS)
                if im.mode not in ("RGB", "L") and ext in (".jpg", ".bmp"):
                    im = im.convert("RGB")
                out.parent.mkdir(parents=True, exist_ok=True)
                if ext in (".jpg", ".webp", ".bmp"):
                    im.save(str(out), quality=q, optimize=True)
                else:
                    im.save(str(out))
                w1, h1 = im.size
        except Exception as e:
            return failed("Image edit failed", f"{type(e).__name__}: {e}")
        verr = _verify_out(out)
        if verr:
            return failed("Output image is not usable", verr)
        with Image.open(out) as chk:
            rw, rh = chk.size
        if act != "convert" and (rw, rh) != (w1, h1):
            return failed("Output size does not match", f"expected {w1}x{h1}, got {rw}x{rh}")
        return done(f"{act}: wrote {out.name}",
                    evidence=f"{w0}x{h0} -> {rw}x{rh}, {out.stat().st_size / 1024:.1f} KB, format={out.suffix.lstrip('.')}")

    # ── audio ─────────────────────────────────────────────────────────────────
    if act in ("audio_info", "audio_convert", "audio_trim", "audio_volume"):
        if _kind(src) != "audio":
            return failed(f"{src.name} is not an audio file",
                          f"extension {src.suffix} is not one of {sorted(AUD_EXTS)}")
        try:
            from pydub import AudioSegment
        except Exception as e:
            return failed("pydub is not available", f"{type(e).__name__}: {e}")
        try:
            seg = AudioSegment.from_file(str(src))
        except Exception as e:
            return failed(f"Could not decode {src.name}",
                          f"{type(e).__name__}: {e}. Install ffmpeg for full format support "
                          f"(winget install Gyan.FFmpeg) or use wav/mp3.")
        dur_ms = len(seg)
        if act == "audio_info":
            return done(f"{src.name}: {dur_ms / 1000:.1f}s at {seg.frame_rate} Hz, {seg.channels} ch",
                        evidence=f"duration={dur_ms}ms, frame_rate={seg.frame_rate}, channels={seg.channels}, "
                                 f"sample_width={seg.sample_width * 8}-bit, size={src.stat().st_size / 1024:.1f} KB")

        fmt = str(args.get("format") or src.suffix.lstrip(".")).lower()
        if fmt not in ("mp3", "wav", "ogg", "flac"):
            return failed(f"Unsupported audio format '{fmt}'", "use mp3, wav, ogg or flac")
        ext = f".{fmt}"
        out = _out(args, src, ext)
        work = seg
        if act == "audio_trim":
            start = max(0, int(args.get("start") or 0))
            dur = int(args.get("duration") or 0)
            if dur > 0:
                work = seg[start * 1000:(start + dur) * 1000]
            else:
                work = seg[start * 1000:]
            if len(work) == 0:
                return failed("Trim produced an empty clip",
                              f"start={start}s duration={dur}s is outside the {dur_ms / 1000:.1f}s source")
        elif act == "audio_volume":
            gain = float(args.get("gain") or 1.0)
            if gain <= 0:
                return failed("Gain must be above 0", f"got {gain}")
            work = seg.apply_gain(20 * __import__("math").log10(gain)) if gain != 1.0 else seg

        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            work.export(str(out), format=fmt)
        except Exception as e:
            return failed(f"Could not export {out.name}",
                          f"{type(e).__name__}: {e}. ffmpeg is required to write {fmt}")
        verr = _verify_out(out)
        if verr:
            return failed("Output audio is not usable", verr)
        from pydub import AudioSegment as _AS
        chk = _AS.from_file(str(out))
        if len(chk) <= 0:
            return failed("Output has zero duration", "export produced silence of 0ms")
        if act == "audio_trim" and abs(len(chk) - len(work)) > 1200:
            return unverified(f"Wrote {out.name} but duration looks off",
                              reason=f"expected ~{len(work)}ms, file has {len(chk)}ms",
                              evidence=f"{len(chk)}ms, {out.stat().st_size / 1024:.1f} KB")
        return done(f"{act}: wrote {out.name}",
                    evidence=f"{dur_ms / 1000:.1f}s -> {len(chk) / 1000:.1f}s, "
                             f"{chk.frame_rate} Hz, {out.stat().st_size / 1024:.1f} KB, format={fmt}")

    # ── video ─────────────────────────────────────────────────────────────────
    if act in ("video_info", "video_frame"):
        if _kind(src) != "video":
            return failed(f"{src.name} is not a video file",
                          f"extension {src.suffix} is not one of {sorted(VID_EXTS)}")
        try:
            import cv2
        except Exception as e:
            return failed("opencv-python is not available", f"{type(e).__name__}: {e}")
        cap = cv2.VideoCapture(str(src))
        if not cap.isOpened():
            return failed(f"Cannot open {src.name}", "the decoder rejected the file")
        try:
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            if act == "video_frame":
                idx = max(0, min(int(args.get("index") or 0), max(0, frames - 1)))
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if not ok or frame is None:
                    return failed("Could not read that frame", f"frame {idx} is unreadable")
                out = _out(args, src, ".png")
                out.parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(out), frame):
                    return failed("Could not write the frame", f"cv2.imwrite failed for {out}")
                verr = _verify_out(out)
                if verr:
                    return failed("Extracted frame is not usable", verr)
                return done(f"Extracted frame {idx} to {out.name}",
                            evidence=f"{w}x{h}, {out.stat().st_size / 1024:.1f} KB")
        finally:
            cap.release()
        if w < 1 or h < 1:
            return unverified(f"{src.name} opened but has no size",
                              reason=f"decoder reported {w}x{h}")
        secs = frames / fps if fps > 0 else 0
        return done(f"{src.name}: {w}x{h} @ {fps:.2f} fps",
                    evidence=f"frames={frames}, fps={fps:.2f}, duration={secs:.1f}s, "
                             f"size={src.stat().st_size / 1024 / 1024:.1f} MB")

    return failed(f"Unknown media_studio action '{act}'",
                  "valid: info convert resize crop rotate compress thumbnail grayscale flip "
                  "audio_info audio_convert audio_trim audio_volume video_info video_frame")


