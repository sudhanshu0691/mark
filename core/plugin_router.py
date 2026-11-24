"""Select registered plugin tools for a goal without additional model calls."""
from __future__ import annotations

import re

RULES = (
    (r"\b(excel|xlsx|xlsm|spreadsheet|workbook)\b", ("excel",)),
    (r"\b(chart|graph|visuali[sz]|dashboard|plot)\w*\b", ("create_visualization",)),
    (r"\b(powerpoint|pptx?|presentation|slides?|deck)\b", ("ppt_studio",)),
    (r"\b(image|photo|picture|audio|video|media|resize|crop)\w*\b", ("media_studio",)),
    (r"\b(workspace|project planning|project plan|work mode|study mode)\b", ("workspace", "notes")),
    (r"\b(notes?|meeting summary|minutes|todo)\b", ("notes",)),
)

def plan(goal: str, declarations: list[dict]) -> str:
    available = {d.get("name") for d in declarations}
    requested = []
    for pattern, tools in RULES:
        if re.search(pattern, goal, re.I):
            for tool in tools:
                if tool not in requested:
                    requested.append(tool)
    if "excel" in requested and re.search(r"\b(analy[sz]|analysis|report|summary|presentation|slides?)\w*\b", goal, re.I):
        if "create_visualization" not in requested:
            requested.insert(requested.index("excel") + 1, "create_visualization")
    if not requested:
        return ""
    order = ("excel", "create_visualization", "ppt_studio", "media_studio", "workspace", "notes")
    requested.sort(key=order.index)
    present = [name for name in requested if name in available]
    missing = [name for name in requested if name not in available]
    return ("Plugin workflow candidates in dependency order: " + ", ".join(present) +
            (". Unavailable registered tools: " + ", ".join(missing) if missing else "") +
            ". Plan only steps supported by the request and actual tool schemas. "
            "Read source data first, pass verified results to subsequent tools, verify outputs and report their full paths. "
            "Workspace manages named app bundles, not task progress; do not claim otherwise. "
            "Do not invent data or tool results; stop dependent steps if prerequisites fail.")
