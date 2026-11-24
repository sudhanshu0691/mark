# Plugins

Drop a `.py` file into this folder and Mark hot-loads it. Copy `_template.py`, rename it without a leading underscore, and implement its tool declaration and `run(args, ctx)` function. Files and folders starting with `_` are ignored. Folder plugins use `plugins/<name>/plugin.py`.

`visualization.py` creates data charts and timelines. Project structure, UML/C4/ERD, flowcharts, sequences, and engineering drawings use the built-in `visualize` action. Generated charts and diagrams are saved under `data/visualisations/`.

`excel/plugin.py` is the canonical workbook capability. It edits `.xlsx` and `.xlsm` files with atomic saves and a timestamped backup before changing an existing workbook. Formula calculation requires Excel or LibreOffice; openpyxl stores formulas but does not evaluate them.

`notification_mode.py` controls Windows notification relay. Use the `notification_mode`
tool with `normal`, `silent` (visible in Mark but not spoken), or `dnd` (hidden from
Mark's notification relay).
