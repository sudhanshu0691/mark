import unittest

from actions.unknown_monitor import Detection, UnknownMonitor


class UnknownMonitorTests(unittest.TestCase):
    def test_alert_requires_one_unknown_for_full_duration(self):
        now = [0.0]
        monitor = UnknownMonitor(duration=10, cooldown=30, clock=lambda: now[0])
        self.assertFalse(monitor.update([Detection(None)]))
        now[0] = 9.99
        self.assertFalse(monitor.update([Detection(None)]))
        now[0] = 10
        self.assertTrue(monitor.update([Detection(None)]))

    def test_known_or_multiple_people_cancel_pending_timer(self):
        now = [0.0]
        monitor = UnknownMonitor(duration=10, clock=lambda: now[0])
        monitor.update([Detection(None)])
        now[0] = 5
        self.assertFalse(monitor.update([Detection("known"), Detection(None)]))
        now[0] = 20
        self.assertFalse(monitor.update([Detection(None)]))

    def test_unreliable_detection_never_becomes_unknown_event(self):
        now = [0.0]
        monitor = UnknownMonitor(duration=1, clock=lambda: now[0])
        monitor.update([Detection(None, reliable=False)])
        now[0] = 2
        self.assertFalse(monitor.update([Detection(None, reliable=False)]))
