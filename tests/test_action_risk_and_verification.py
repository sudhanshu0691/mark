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
