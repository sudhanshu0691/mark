import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui import MarkUI


def test_mark_ui_registers_full_size_vision_workspace():
    app = QApplication.instance() or QApplication([])
    window = MarkUI()
    assert window.name == "MARK"
    assert "vision" in window.stage.panels
    assert "camera" not in window.stage.panels
    window._ensure_vision()
    assert window.vision_dashboard is not None
    assert window.vision_dashboard.start_button.text() == "Start Monitoring"
    assert window.vision_dashboard.stop_button.text() == "Stop Monitoring"
    window.close()
    app.processEvents()


def test_microphone_starts_enabled_and_can_be_muted():
    app = QApplication.instance() or QApplication([])
    window = MarkUI()
    try:
        assert window.muted is False
        assert window.mic_button.toolTip() == 'Microphone on'
        window._toggle_mic()
        assert window.muted is True
        assert window.mic_button.toolTip() == 'Microphone off'
        window._toggle_mic()
        assert window.muted is False
    finally:
        window.close()
        app.processEvents()


def test_gesture_buttons_dispatch_separate_commands():
    from unittest.mock import Mock
    app = QApplication.instance() or QApplication([])
    window = MarkUI()
    try:
        callback = Mock()
        window.on_gesture_action = callback
        window.gesture_enable_button.click()
        window.gesture_disable_button.click()
        assert [call.args for call in callback.call_args_list] == [('enable',), ('disable',)]
        assert window.gesture_status_label.text().startswith('GESTURE')
    finally:
        window.close()
        app.processEvents()


def test_gemini_live_and_text_status_are_independent():
    app = QApplication.instance() or QApplication([])
    window = MarkUI()
    try:
        window.set_llm_provider('ollama')
        window.set_gemini_live_state('READY')
        assert window._llm_labels['ollama'].text() == 'OLLAMA TEXT - WORKING'
        assert window._llm_labels['gemini'].text() == 'GEMINI TEXT - IDLE'
        assert window.gemini_live_label.text() == 'GEMINI LIVE - CONNECTED'
        window.set_llm_provider('sleep')
        assert window.gemini_live_label.text() == 'GEMINI LIVE - CONNECTED'
        window.set_gemini_live_state('DEGRADED')
        assert window.gemini_live_label.text() == 'GEMINI LIVE - RECONNECTING'
        window.set_gemini_live_state('IDLE')
        assert window.gemini_live_label.text() == 'GEMINI LIVE - IDLE'
    finally:
        window.close()
        app.processEvents()
