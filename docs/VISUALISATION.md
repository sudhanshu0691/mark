# Diagrams and drawings

Ask: "is folder ka diagram bana do" + the path. Tool: `visualize` (actions/visualize.py; implementation in actions/viz/). No Java or Graphviz needed (Pillow + ezdxf only).

| Request | Output | Standard / notation |
| --- | --- | --- |
| diagram (auto) | component, package, module, class, ERD (only what the project has) | UML 2.5 (ISO/IEC 19505), C4 model, crow's foot ERD |
| flowchart | from a spec, or from a Python function | ISO 5807 symbols |
| sequence | from a spec | UML 2.5 |
| drawing | 2D drawing from a spec: lines, rects, circles, arcs, polylines, text, dimensions | ISO 5457 sheet, ISO 7200 title block, ISO 128 line types, ISO 129 dimensioning, ISO 3098 lettering |

Every sheet has the frame, centring marks, zone grid, title block (title, notation, document no., author, date, revision, sheet, size, scale) and a legend. Files: SVG + PNG + PDF (+ DXF for drawings) in `data/visualisations/`.
The tool says DONE only when all files exist and the layout check found no overlapping boxes.

Reads: Python (ast), JS/TS and Java (imports, classes), `.sql` files and SQLite databases (tables, foreign keys), ORM-style models.
Not supported yet: use-case, state, activity and deployment diagrams, BPMN, 3D. The sheet values follow common ISO practice - check them against your organisation's standard. A drawing made from a description must be checked by a responsible person.
