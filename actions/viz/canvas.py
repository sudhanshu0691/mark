"""Two drawing back-ends with ONE interface, so every diagram is drawn once and exported as SVG, PNG and PDF."""
from __future__ import annotations

import math
import os
import sys
from functools import lru_cache
from html import escape

_FONT_CANDIDATES = {
    False: ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf"],
    True: ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}
_MONO = ["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False, mono: bool = False):
    from PIL import ImageFont
    names = _MONO if mono else _FONT_CANDIDATES[bold]
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                          # very old Pillow
        return ImageFont.load_default()


def text_width(s: str, size: float, bold: bool = False, mono: bool = False) -> float:
    f = font(max(1, int(round(size))), bold, mono)
    try:
        return float(f.getlength(s))
    except AttributeError:
        return float(f.getsize(s)[0])


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


class SvgCanvas:
    def __init__(self, w: float, h: float, bg: str = "#ffffff"):
        self.w, self.h = w, h
        self.parts: list[str] = [f'<rect x="0" y="0" width="{w:.1f}" height="{h:.1f}" fill="{bg}"/>']

    @staticmethod
    def _dash(dash) -> str:
        return f' stroke-dasharray="{dash[0]:.1f} {dash[1]:.1f}"' if dash else ""

    def line(self, p1, p2, color="#000", width=1.0, dash=None):
        self.parts.append(f'<line x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}" stroke="{color}" stroke-width="{width:.2f}"{self._dash(dash)}/>')

    def polyline(self, pts, color="#000", width=1.0, dash=None):
        s = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        self.parts.append(f'<polyline points="{s}" fill="none" stroke="{color}" stroke-width="{width:.2f}" stroke-linejoin="round"{self._dash(dash)}/>')

    def polygon(self, pts, fill="none", stroke="#000", width=1.0):
        s = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        self.parts.append(f'<polygon points="{s}" fill="{fill}" stroke="{stroke}" stroke-width="{width:.2f}" stroke-linejoin="round"/>')

    def rect(self, x, y, w, h, fill="none", stroke="#000", width=1.0, radius=0.0, dash=None):
        self.parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{radius:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{width:.2f}"{self._dash(dash)}/>')

    def ellipse(self, cx, cy, rx, ry, fill="none", stroke="#000", width=1.0):
        self.parts.append(f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{width:.2f}"/>')

    def arc(self, cx, cy, r, a0, a1, color="#000", width=1.0):
        """Counter-clockwise arc a0..a1 degrees (math convention, y up in drawings -> flipped by caller)."""
        n = max(8, int(abs(a1 - a0) / 4))
        pts = [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cy - r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
        self.polyline(pts, color, width)

    def text(self, x, y, s, size=13, color="#000", anchor="middle", bold=False, mono=False, italic=False, rotate=0):
        fam = "Consolas, 'DejaVu Sans Mono', monospace" if mono else "'Segoe UI', Arial, 'DejaVu Sans', sans-serif"
        extra = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate else ""
        weight = ' font-weight="bold"' if bold else ""
        style = ' font-style="italic"' if italic else ""
        self.parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}" font-size="{size:.1f}" fill="{color}" '
                          f'text-anchor="{anchor}" dominant-baseline="central"{weight}{style}{extra}>{escape(str(s))}</text>')

    def tostring(self) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w:.0f}" height="{self.h:.0f}" viewBox="0 0 {self.w:.1f} {self.h:.1f}">\n'
                + "\n".join(self.parts) + "\n</svg>\n")


class PilCanvas:
    """Draws at `ss`x supersampling, then downsamples (anti-aliased PNG / PDF)."""

    def __init__(self, w: float, h: float, bg: str = "#ffffff", ss: int = 2):
        from PIL import Image, ImageDraw
        self.w, self.h, self.ss = w, h, ss
        self.img = Image.new("RGB", (max(1, int(w * ss)), max(1, int(h * ss))), _rgb(bg))
        self.d = ImageDraw.Draw(self.img)

    def _p(self, p):
        return (p[0] * self.ss, p[1] * self.ss)

    def _stroke_dashed(self, pts, color, width, dash):
        if not dash:
            self.d.line([self._p(p) for p in pts], fill=_rgb(color), width=max(1, int(round(width * self.ss))), joint="curve")
            return
        on, off = dash
        for a, b in zip(pts, pts[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy)
            if L == 0:
                continue
            ux, uy = dx / L, dy / L
            pos = 0.0
            while pos < L:
                e = min(pos + on, L)
                self.d.line([self._p((a[0] + ux * pos, a[1] + uy * pos)), self._p((a[0] + ux * e, a[1] + uy * e))],
                            fill=_rgb(color), width=max(1, int(round(width * self.ss))))
                pos += on + off

    def line(self, p1, p2, color="#000", width=1.0, dash=None):
        self._stroke_dashed([p1, p2], color, width, dash)

    def polyline(self, pts, color="#000", width=1.0, dash=None):
        self._stroke_dashed(list(pts), color, width, dash)

    def polygon(self, pts, fill="none", stroke="#000", width=1.0):
        P = [self._p(p) for p in pts]
        self.d.polygon(P, fill=None if fill == "none" else _rgb(fill))
        if stroke != "none":
            self.d.line(P + [P[0]], fill=_rgb(stroke), width=max(1, int(round(width * self.ss))), joint="curve")

    def rect(self, x, y, w, h, fill="none", stroke="#000", width=1.0, radius=0.0, dash=None):
        box = [x * self.ss, y * self.ss, (x + w) * self.ss, (y + h) * self.ss]
        if dash:
            if fill != "none":
                self.d.rectangle(box, fill=_rgb(fill))
            self._stroke_dashed([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)], stroke, width, dash)
            return
        kw = dict(fill=None if fill == "none" else _rgb(fill), outline=None if stroke == "none" else _rgb(stroke), width=max(1, int(round(width * self.ss))))
        if radius > 0:
            self.d.rounded_rectangle(box, radius=radius * self.ss, **kw)
        else:
            self.d.rectangle(box, **kw)

    def ellipse(self, cx, cy, rx, ry, fill="none", stroke="#000", width=1.0):
        self.d.ellipse([(cx - rx) * self.ss, (cy - ry) * self.ss, (cx + rx) * self.ss, (cy + ry) * self.ss],
                       fill=None if fill == "none" else _rgb(fill), outline=None if stroke == "none" else _rgb(stroke), width=max(1, int(round(width * self.ss))))

    def arc(self, cx, cy, r, a0, a1, color="#000", width=1.0):
        n = max(8, int(abs(a1 - a0) / 4))
        pts = [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cy - r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
        self.polyline(pts, color, width)

    def text(self, x, y, s, size=13, color="#000", anchor="middle", bold=False, mono=False, italic=False, rotate=0):
        from PIL import Image, ImageDraw
        f = font(max(1, int(round(size * self.ss))), bold, mono)
        s = str(s)
        wid = f.getlength(s) if hasattr(f, "getlength") else f.getsize(s)[0]
        asc = int(size * self.ss * 1.4)
        if rotate:
            tile = Image.new("RGBA", (int(wid) + 8, asc * 2), (0, 0, 0, 0))
            ImageDraw.Draw(tile).text((4, asc), s, font=f, fill=_rgb(color) + (255,), anchor="lm")
            tile = tile.rotate(-rotate, expand=True, resample=Image.BICUBIC)
            self.img.paste(tile, (int(x * self.ss - tile.width / 2), int(y * self.ss - tile.height / 2)), tile)
            return
        ax = {"start": "l", "middle": "m", "end": "r"}[anchor]
        self.d.text((x * self.ss, y * self.ss), s, font=f, fill=_rgb(color), anchor=ax + "m")

    def finish(self, zoom: float = 1.0):
        from PIL import Image
        return self.img.resize((max(1, int(self.w * zoom)), max(1, int(self.h * zoom))), Image.LANCZOS)
