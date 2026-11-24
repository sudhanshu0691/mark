from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio

import pytest
from PIL import Image
from core import risk, interaction_verify
from core.task_manager import TaskManager


@pytest.mark.parametrize('tool,args', [
    ('computer_settings', {'action': 'zoom_in'}),
    ('computer_settings', {'action': 'scroll_down'}),
    ('computer_control', {'action': 'scroll'}),
    ('computer_control', {'action': 'click'}),
    ('computer_control', {'action': 'move'}),
    ('file_controller', {'action': 'open'}),
    ('open_app', {'app_name': 'Notepad'}),
    ('window_manager', {'action': 'focus'}),
])
def test_low_risk_does_not_request_approval(tool, args):
    assert risk.classify(tool, args) == risk.LOW
    assert not risk.requires_confirmation(tool, args)


@pytest.mark.parametrize('tool,args', [
    ('file_controller', {'action': 'delete'}),
    ('send_message', {}),
    ('computer_settings', {'action': 'shutdown'}),
])
def test_sensitive_actions_require_approval(tool, args):
    assert risk.requires_confirmation(tool, args)


@pytest.mark.parametrize('action', ['zoom_in', 'scroll_down'])
def test_viewport_verification(action, monkeypatch):
    monkeypatch.setattr(interaction_verify.time, 'sleep', lambda _: None)
    before = (1, Image.new('RGB', (20, 20), 'black'))
    changed = (1, Image.new('RGB', (20, 20), 'white'))
    assert interaction_verify.verify(before, {'action': action}, '[UNVERIFIED] ran', lambda: changed).startswith('[DONE]')
    assert interaction_verify.verify(before, {'action': action}, '[UNVERIFIED] ran', lambda: before).startswith('[UNVERIFIED]')
    assert interaction_verify.verify(before, {'action': action}, '[FAILED] error', lambda: changed).startswith('[FAILED]')
    assert interaction_verify.verify(before, {'action': action}, '[UNVERIFIED] ran', lambda: (2, changed[1])).startswith('[UNVERIFIED]')


def test_execution_verification_and_approval_are_independent(monkeypatch, tmp_path):
    monkeypatch.setattr('core.task_manager._EVENT_LOG', tmp_path / 'events')
    tasks = TaskManager()
    tasks.start('1', 'computer_control', {'action': 'scroll'})
    tasks.set_approval_required('1', False)
    tasks.finish('1', '[UNVERIFIED] ran', 'UNVERIFIED')
    row = tasks.recent()[0]
    assert (row['execution_status'], row['verification_status'], row['approval_required']) == ('EXECUTED', 'UNVERIFIED', False)


@pytest.mark.parametrize('tool,args', [
    ('computer_control', {'action': 'left_click'}),
    ('computer_control', {'action': 'press', 'key': 'down'}),
    ('computer_control', {'action': 'hotkey', 'keys': 'ctrl+equal'}),
    ('browser_control', {'action': 'go_to'}),
    ('browser_control', {'action': 'smart_click'}),
    ('excel', {'action': 'inspect'}),
    ('excel', {'action': 'set_cell'}),
])
def test_common_actions_do_not_require_approval(tool, args):
    assert not risk.requires_confirmation(tool, args)


@pytest.mark.parametrize('tool,args', [
    ('computer_control', {'action': 'hotkey', 'keys': 'shift+delete'}),
    ('computer_control', {'action': 'press', 'key': 'enter'}),
    ('excel', {'action': 'remove_duplicates'}),
    ('computer_settings', {'action': 'empty_recycle_bin'}),
])
def test_sensitive_and_unknown_keyboard_actions_still_require_approval(tool, args):
    assert risk.requires_confirmation(tool, args)


def test_common_excel_action_executes_without_prompt(monkeypatch):
    import main
    from unittest.mock import AsyncMock, Mock
    from types import SimpleNamespace
    import asyncio
    ui = Mock()
    ui.task_start.return_value = None
    ui.confirm_action = AsyncMock(side_effect=AssertionError('Unexpected approval'))
    live = main.MarkLive(ui)
    live.plugins = Mock()
    live.plugins.has_tool.return_value = True
    live.plugins.run.return_value = '[DONE] Workbook inspected'
    live._all_declarations = lambda: [{'name': 'excel', 'parameters': {'properties': {'action': {'type': 'STRING'}}, 'required': ['action']}}]
    live._report_verification = AsyncMock(return_value='PASS')
    result = asyncio.run(live._execute_tool(SimpleNamespace(name='excel', args={'action': 'inspect'}, id='test')))
    assert result.response['result'].startswith('[DONE]')
    ui.confirm_action.assert_not_awaited()
    live.plugins.run.assert_called_once()


def test_typing_verification_requires_new_text_in_same_control(monkeypatch):
    monkeypatch.setattr(interaction_verify.time, "sleep", lambda _: None)
    args = {"action": "type_text", "text": "leave application"}
    before = (1, (10, 20), "Existing text")
    assert interaction_verify.supports("computer_settings", args)
    assert interaction_verify.verify_text(before, args, "[UNVERIFIED] ran", lambda: (1, (10, 20), "Existing text leave application")).startswith("[DONE]")
    assert interaction_verify.verify_text((1, (10, 20), "leave application"), args, "[UNVERIFIED] ran", lambda: (1, (10, 20), "leave application")).startswith("[UNVERIFIED]")
    assert interaction_verify.verify_text(before, args, "[UNVERIFIED] ran", lambda: (2, (10, 20), "leave application")).startswith("[UNVERIFIED]")
    assert interaction_verify.verify_text(None, args, "[UNVERIFIED] ran").startswith("[UNVERIFIED]")
    assert interaction_verify.verify_text(before, args, "[FAILED] error").startswith("[FAILED]")


def test_monitor_discards_frame_when_disabled():
    import time
    from core.screen_awareness import ScreenMonitor
    monitor = ScreenMonitor()
    monitor._frame = Image.new("RGB", (4, 4))
    monitor._state["frame_timestamp"] = time.monotonic()
    assert monitor.latest_frame() is not None
    monitor.set_enabled(False)
    assert monitor.latest_frame() is None
    assert monitor._frame is None
