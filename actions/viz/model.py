"""Neutral diagram model: nodes, edges, graph. Builders fill it, layout/render consume it."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    id: str
    label: str
    kind: str = "box"                      # package|class|box|entity|db|actor|terminator|process|decision|io|document|predefined|connector
    sections: list[list[str]] = field(default_factory=list)   # extra compartments (class attrs/methods, entity columns, C4 description)
    stereotype: str = ""                   # shown as «stereotype»
    fill: str = ""                         # optional colour override "#rrggbb"
    # filled by layout
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    layer: int = 0
    dummy: bool = False

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class Edge:
    src: str
    dst: str
    kind: str = "dependency"               # dependency|inherit|assoc|flow|fk|realize|compose|aggregate
    label: str = ""
    src_end: str = ""                      # decoration at the src end: ""|one|many|zero_one|zero_many
    dst_end: str = ""
    points: list[tuple[float, float]] = field(default_factory=list)   # filled by layout


@dataclass
class Graph:
    title: str
    notation: str                          # e.g. "UML 2.5 (ISO/IEC 19505) - Package diagram"
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    direction: str = "TB"                  # TB | LR
    legend: list[tuple[str, str]] = field(default_factory=list)       # (symbol-kind, meaning)
    notes: list[str] = field(default_factory=list)
    kind: str = "graph"                    # graph | sequence

    def node(self, nid: str) -> Node | None:
        return next((n for n in self.nodes if n.id == nid), None)
