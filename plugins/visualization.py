"""Generate a real PNG visualisation (chart/timeline) and save it to disk."""
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont
from core.paths import VIZ_DIR

from core.result import done, failed

NAME = "visualization_tools"
DESCRIPTION = "Create PNG data charts (bar, line, pie, 3D charts) or timelines and save them to disk. For project architecture, flowcharts, sequence diagrams, or engineering drawings, use the visualize action."
METADATA = {"version": "1.0.0", "capabilities": ["visualization", "chart", "timeline", "image"], "permissions": [], "timeout": 60}

DECLARATION = {
    "name": "create_visualization",
    "description": "Create a PNG data chart or timeline (not the hologram UI). Use for bar, line, pie, bar3d, scatter3d charts, or timelines. For project architecture, flowcharts, sequence diagrams, and engineering drawings, use the visualize action.",
    "parameters": {"type": "OBJECT", "properties": {
        "type": {"type": "STRING", "description": "bar | line | pie | timeline | bar3d | scatter3d"},
        "title": {"type": "STRING", "description": "Chart title"},
        "labels": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Labels"},
        "values": {"type": "ARRAY", "items": {"type": "NUMBER"}, "description": "Values"},
        "items": {"type": "ARRAY", "items": {"type": "OBJECT"}, "description": "Timeline items [{label,start,end}]"},
        "output_name": {"type": "STRING", "description": "Optional file name"},
    }, "required": ["type"]},
}
def _font(size=16):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _bar(title, labels, values):
    w, h = 900, 540
    img = Image.new("RGB", (w, h), (12, 18, 32))
    d = ImageDraw.Draw(img)
    d.text((w // 2, 30), title or "Bar Chart", fill=(120, 226, 255), font=_font(24), anchor="mt")
    if not values:
        return img
    n = len(values)
    maxv = max(values) if values else 1
    if maxv == 0:
        maxv = 1
    margin = 60
    plot_h = h - 140
    bar_w = (w - 2 * margin) // max(1, n)
    for i, v in enumerate(values):
        x0 = margin + i * bar_w + 8
        x1 = x0 + bar_w - 16
        bh = int(plot_h * (v / maxv))
        y0 = h - 80 - bh
        y1 = h - 80
        d.rectangle([x0, y0, x1, y1], fill=(60, 205, 255))
        lab = labels[i] if i < len(labels) else str(i + 1)
        d.text(((x0 + x1) // 2, y1 + 6), str(lab)[:12], fill=(200, 230, 255), font=_font(12), anchor="mt")
        d.text(((x0 + x1) // 2, y0 - 4), f"{v:g}", fill=(255, 255, 255), font=_font(11), anchor="mb")
    return img

def _line(title, labels, values):
    w, h = 900, 540
    img = Image.new("RGB", (w, h), (12, 18, 32))
    d = ImageDraw.Draw(img)
    d.text((w // 2, 30), title or "Line Chart", fill=(120, 226, 255), font=_font(24), anchor="mt")
    if not values:
        return img
    n = len(values)
    maxv = max(values) if values else 1
    minv = min(values) if values else 0
    if maxv == minv:
        maxv = minv + 1
    margin = 60
    plot_w = w - 2 * margin
    plot_h = h - 140
    pts = []
    for i, v in enumerate(values):
        x = margin + int(plot_w * (i / max(1, n - 1)))
        y = h - 80 - int(plot_h * ((v - minv) / (maxv - minv)))
        pts.append((x, y))
    for i in range(len(pts) - 1):
        d.line([pts[i], pts[i + 1]], fill=(60, 205, 255), width=3)
    for (x, y), v in zip(pts, values):
        d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(255, 176, 64))
    for i, lab in enumerate(labels):
        if i < len(pts):
            d.text((pts[i][0], h - 70), str(lab)[:10], fill=(200, 230, 255), font=_font(11), anchor="mt")
    return img


def _pie(title, labels, values):
    w, h = 700, 540
    img = Image.new("RGB", (w, h), (12, 18, 32))
    d = ImageDraw.Draw(img)
    d.text((w // 2, 30), title or "Pie Chart", fill=(120, 226, 255), font=_font(24), anchor="mt")
    total = sum(values) if values else 0
    if total <= 0:
        return img
    cx, cy, r = w // 2, h // 2 + 20, 150
    start = 0
    colors = [(60, 205, 255), (255, 176, 64), (170, 110, 255), (40, 255, 150), (255, 122, 24), (255, 72, 52)]
    for i, v in enumerate(values):
        end = start + 360 * (v / total)
        d.pieslice([cx - r, cy - r, cx + r, cy + r], start=start, end=end, fill=colors[i % len(colors)], outline=(12, 18, 32))
        start = end
    lx, ly = 30, h - 120
    for i, (lab, v) in enumerate(zip(labels, values)):
        col = colors[i % len(colors)]
        d.rectangle([lx, ly + i * 18, lx + 12, ly + i * 18 + 12], fill=col)
        d.text((lx + 18, ly + i * 18), f"{lab}: {v:g}", fill=(200, 230, 255), font=_font(12))
    return img

def _scatter3d(title, labels, values):
    # values: list of [x,y,z] or flat list; labels optional
    w, h = 900, 560
    img = Image.new("RGB", (w, h), (12, 18, 32))
    d = ImageDraw.Draw(img)
    d.text((w // 2, 24), title or "3D Scatter", fill=(120, 226, 255), font=_font(24), anchor="mt")
    pts3 = []
    if values and isinstance(values[0], (list, tuple)):
        for v in values:
            pts3.append((float(v[0]), float(v[1]), float(v[2]) if len(v) > 2 else 0.0))
    else:
        for i, v in enumerate(values):
            pts3.append((float(i), float(v), 0.0))
    if not pts3:
        return img
    xs = [p[0] for p in pts3]; ys = [p[1] for p in pts3]; zs = [p[2] for p in pts3]
    def norm(arr):
        lo, hi = min(arr), max(arr)
        if hi == lo:
            hi = lo + 1
        return [(a - lo) / (hi - lo) * 2 - 1 for a in arr]
    nx, ny, nz = norm(xs), norm(ys), norm(zs)
    cx, cy = w // 2, h // 2 + 40
    scale = 160
    # axes
    for a, b in [((-1, 0, 0), (1, 0, 0)), ((0, -1, 0), (0, 1, 0)), ((0, 0, -1), (0, 0, 1))]:
        p0 = _proj(a[0], a[1], a[2], cx, cy, scale)
        p1 = _proj(b[0], b[1], b[2], cx, cy, scale)
        d.line([p0, p1], fill=(60, 90, 130))
    for (x, y, z) in zip(nx, ny, nz):
        px, py = _proj(x, y, z, cx, cy, scale)
        d.ellipse([px - 5, py - 5, px + 5, py + 5], fill=(255, 176, 64), outline=(12, 18, 32))
    return img

def _proj(x, y, z, cx, cy, scale=1.0):
    # simple isometric projection
    X = cx + (x - z) * 0.7 * scale
    Y = cy + (x + z) * 0.4 * scale - y * scale
    return X, Y


def _bar3d(title, labels, values):
    w, h = 900, 560
    img = Image.new("RGB", (w, h), (12, 18, 32))
    d = ImageDraw.Draw(img)
    d.text((w // 2, 24), title or "3D Bar Chart", fill=(120, 226, 255), font=_font(24), anchor="mt")
    if not values:
        return img
    n = len(values)
    maxv = max(values) if values else 1
    if maxv == 0:
        maxv = 1
    cx, cy = w // 2, h // 2 + 80
    scale = 180 / max(1, n)
    bw = 0.6
    for i, v in enumerate(values):
        x = (i - (n - 1) / 2) * 1.2
        z = 0
        yh = (v / maxv) * 2.0
        # four vertical edges
        corners = [(x - bw / 2, 0, z - bw / 2), (x + bw / 2, 0, z - bw / 2),
                   (x + bw / 2, 0, z + bw / 2), (x - bw / 2, 0, z + bw / 2)]
        top = [(c[0], yh, c[2]) for c in corners]
        bot_pts = [_proj(c[0], c[1], c[2], cx, cy, scale) for c in corners]
        top_pts = [_proj(c[0], c[1], c[2], cx, cy, scale) for c in top]
        # draw sides
        for j in range(4):
            k = (j + 1) % 4
            d.polygon([bot_pts[j], bot_pts[k], top_pts[k], top_pts[j]], fill=(40, 120, 180), outline=(12, 18, 32))
        # top face
        d.polygon(top_pts, fill=(60, 205, 255), outline=(12, 18, 32))
        lab = labels[i] if i < len(labels) else str(i + 1)
        tx, ty = _proj(x, yh + 0.2, z, cx, cy, scale)
        d.text((tx, ty), f"{lab}: {v:g}", fill=(200, 230, 255), font=_font(11), anchor="mt")
    # ground grid
    for g in range(-3, 4):
        a = _proj(g, 0, -3, cx, cy, scale)
        b = _proj(g, 0, 3, cx, cy, scale)
        d.line([a, b], fill=(40, 60, 90))
        a = _proj(-3, 0, g, cx, cy, scale)
        b = _proj(3, 0, g, cx, cy, scale)
        d.line([a, b], fill=(40, 60, 90))
    return img

def _to_minutes(t):
    t = str(t).strip()
    if ":" in t:
        parts = t.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return h * 60 + m
    try:
        return int(float(t))
    except Exception:
        return 0


def _timeline(title, items):
    w, h = 1000, 560
    img = Image.new("RGB", (w, h), (12, 18, 32))
    d = ImageDraw.Draw(img)
    d.text((w // 2, 30), title or "Timeline", fill=(120, 226, 255), font=_font(24), anchor="mt")
    if not items:
        return img
    starts = [_to_minutes(it.get("start", "0")) for it in items]
    ends = [_to_minutes(it.get("end", it.get("start", "0"))) for it in items]
    lo = min(starts) if starts else 0
    hi = max(ends) if ends else lo + 60
    if hi <= lo:
        hi = lo + 60
    span = hi - lo
    margin_l, margin_r = 180, 60
    plot_w = w - margin_l - margin_r
    top = 80
    row_h = 34
    for i, it in enumerate(items):
        s = _to_minutes(it.get("start", "0"))
        e = _to_minutes(it.get("end", it.get("start", "0")))
        if e <= s:
            e = s + 5
        x0 = margin_l + int(plot_w * ((s - lo) / span))
        x1 = margin_l + int(plot_w * ((e - lo) / span))
        y0 = top + i * row_h
        y1 = y0 + 22
        d.rectangle([x0, y0, max(x0 + 4, x1), y1], fill=(60, 205, 255))
        label = str(it.get("label", f"Item {i+1}"))[:26]
        d.text((10, y0 + 4), label, fill=(200, 230, 255), font=_font(13))
        d.text((x0 + 4, y0 + 4), f"{it.get('start','')}–{it.get('end','')}", fill=(12, 18, 32), font=_font(11))
    ax_y = top + len(items) * row_h + 10
    d.line([(margin_l, ax_y), (w - margin_r, ax_y)], fill=(80, 120, 160), width=1)
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        xm = margin_l + int(plot_w * frac)
        mins = lo + int(span * frac)
        hh, mm = divmod(mins, 60)
        d.text((xm, ax_y + 4), f"{hh:02d}:{mm:02d}", fill=(160, 200, 230), font=_font(11), anchor="mt")
    return img


def run(args, ctx):
    vtype = str(args.get("type", "bar")).lower().strip()
    title = str(args.get("title", "")).strip()
    labels = args.get("labels") or []
    values = args.get("values") or []
    items = args.get("items") or []
    try:
        values = [float(x) for x in values]
    except Exception:
        values = []
    try:
        labels = [str(x) for x in labels]
    except Exception:
        labels = []
    out_dir = VIZ_DIR
    if vtype in ("bar", "bar3d"):
        img = _bar3d(title, labels, values) if vtype == "bar3d" else _bar(title, labels, values)
    elif vtype == "scatter3d":
        img = _scatter3d(title, labels, values)
    elif vtype == "line":
        img = _line(title, labels, values)
    elif vtype == "pie":
        img = _pie(title, labels, values)
    elif vtype == "timeline":
        img = _timeline(title, items)
    else:
        return failed("Unknown chart type", f"'{vtype}' not supported (bar/line/pie/timeline/bar3d/scatter3d)")
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = (args.get("output_name") or title or vtype).strip().replace(" ", "_")[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{ts}_{slug}.png"
    img.save(path, "PNG")
    try:
        if getattr(ctx, "ui", None) is not None:
            ctx.ui.show_camera_frame(path.read_bytes())
    except Exception:
        pass
    return done(f"Chart saved: {path}\nOpen the file to view it.")

