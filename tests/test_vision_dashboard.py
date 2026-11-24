import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui import MarkUI


def test_mark_ui_registers_full_size_vision_workspace():
    app = QApplication.instance() or QApplication([])
    window = MarkUI()
    assert window.name == "MARK"
    assert "vision" in window.stage.panels
    window._ensure_vision()
    assert window.vision_dashboard is not None
    assert window.vision_dashboard.start_button.text() == "Start Monitoring"
    assert window.vision_dashboard.stop_button.text() == "Stop Monitoring"
    window.close()
    app.processEvents()
