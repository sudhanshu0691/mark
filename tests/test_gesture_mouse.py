from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from actions.gesture_mouse import GestureMouse
from core.intent import match_local_command


def hand(raised=(True, False, False, False), pinch=None):
    p = [SimpleNamespace(x=0.5, y=0.8) for _ in range(21)]
    p[0] = SimpleNamespace(x=0.5, y=1.0)
    p[5], p[17] = SimpleNamespace(x=0.3, y=0.7), SimpleNamespace(x=0.7, y=0.7)
    for (tip, joint, x), up in zip(((8, 6, .35), (12, 10, .45), (16, 14, .55), (20, 18, .65)), raised):
        p[joint] = SimpleNamespace(x=x, y=.65)
        p[tip] = SimpleNamespace(x=x, y=.3 if up else .85)
    p[4] = SimpleNamespace(x=.9, y=.7)
    if pinch:
        tip = p[8 if pinch == 'left' else 12]
        p[4] = SimpleNamespace(x=tip.x + .01, y=tip.y)
    return p


@pytest.fixture
def controller():
    mouse = Mock()
    mouse.size.return_value = (1920, 1080)
    now = [1.0]
    statuses = []
    ctrl = GestureMouse(status=statuses.append, mouse=mouse, clock=lambda: now[0])
    ctrl._enabled = True
    return ctrl, mouse, now, statuses


def test_move_and_debounced_left_and_right_click(controller):
    c, m, clock, _ = controller
    c.process(hand())
    assert m.moveTo.called
    for button in ('left', 'right'):
        c.process(hand(pinch=button))
        clock[0] += .25
        c.process(hand())
        m.click.assert_called_with(button=button)
        clock[0] += .3
    assert m.click.call_count == 2


def test_drag_is_opt_in_and_palm_releases(controller):
    c, m, clock, statuses = controller
    c.drag_mode = True
    c.process(hand(pinch='left'))
    clock[0] += 1
    c.process(hand(pinch='left'))
    m.mouseDown.assert_called_once_with(button='left')
    c.process(hand((True, True, True, True)))
    m.mouseUp.assert_called_once_with(button='left')
    assert statuses[-1] == 'PAUSED: OPEN PALM'
    m.click.assert_not_called()


def fist():
    points = hand((False, False, False, False))
    points[4] = SimpleNamespace(x=.5, y=.78)
    return points


def test_emergency_fist_pauses_immediately_and_disables_after_hold(controller):
    c, m, clock, statuses = controller
    c._dragging = True
    c.process(fist())
    assert c._enabled and statuses[-1] == 'PAUSED: HOLD FIST TO STOP'
    m.mouseUp.assert_called_once_with(button='left')
    clock[0] += .4
    c.process(fist())
    assert c._enabled
    clock[0] += .5
    c.process(fist())
    assert not c._enabled and statuses[-1] == 'OFF: EMERGENCY STOP'
    c.process(hand())
    m.moveTo.assert_not_called()
    m.click.assert_not_called()


def test_transient_fist_and_open_thumb_do_not_disable(controller):
    c, m, clock, _ = controller
    c.process(fist())
    clock[0] += .5
    c.process(hand())
    clock[0] += 1
    c.process(fist())
    assert c._enabled
    c.process(hand((False, False, False, False)))
    clock[0] += 2
    c.process(hand((False, False, False, False)))
    assert c._enabled


def test_two_finger_scroll(controller):
    c, m, _, _ = controller
    h = hand((True, True, False, False))
    c.process(h)
    h[8].y -= .1
    h[12].y -= .1
    c.process(h)
    m.scroll.assert_called_once_with(1)
    m.click.assert_not_called()


def test_quick_pinch_does_not_click_and_disable_releases(controller):
    c, m, _, _ = controller
    c.process(hand(pinch='left'))
    c.process(hand())
    m.click.assert_not_called()
    c._dragging = True
    c.disable()
    m.mouseUp.assert_called_once_with(button='left')


@pytest.mark.parametrize('text,action', [('MARK, enable gesture mouse', 'enable'), ('disable gesture mouse', 'disable'), ('increase mouse sensitivity', 'sensitivity_up'), ('enable drag mode', 'drag_on')])
def test_local_commands(text, action):
    assert match_local_command(text) == ('gesture_mouse', {'action': action})

def test_missing_tracking_dependency_never_enables(monkeypatch):
    import sys
    mouse = Mock()
    ctrl = GestureMouse(mouse=mouse)
    monkeypatch.setitem(sys.modules, 'mediapipe', None)
    with pytest.raises(RuntimeError, match='requirements-gesture'):
        ctrl.enable()
    assert not ctrl._enabled
    mouse.assert_not_called()


def test_camera_stop_disables_and_releases_drag(controller):
    c, m, _, statuses = controller
    c._dragging = True
    c.disable('OFF: CAMERA STOPPED')
    assert not c._enabled
    assert statuses[-1] == 'OFF: CAMERA STOPPED'
    m.mouseUp.assert_called_once_with(button='left')


def test_camera_and_gesture_start_independently():
    from main import MarkLive
    live = MarkLive.__new__(MarkLive)
    live.ui = Mock()
    live.gesture_mouse = Mock()
    live._gesture_source = Mock()
    live._start_default_camera_features()
    live.ui._post.assert_called_once_with(live.ui.start_camera_stream)
    live.gesture_mouse.enable.assert_called_once()
    live._gesture_source.subscribe.assert_called_once()
    # Monitoring startup does not depend on gesture dependencies.
    live.ui.reset_mock()
    live.gesture_mouse.enable.side_effect = RuntimeError('missing')
    live._start_default_camera_features()
    live.ui._post.assert_called_once_with(live.ui.start_camera_stream)
    live.ui.show_gesture_status.assert_called_with('OFF: DEPENDENCY UNAVAILABLE')
