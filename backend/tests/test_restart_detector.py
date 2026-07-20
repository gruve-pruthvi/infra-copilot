from collections import defaultdict, deque

from app.services.monitor import detect_restart_loops


def _summaries(count):
    return [{"name": "crashy", "restart_count": count}]


def test_three_restarts_in_window_triggers():
    events = defaultdict(lambda: deque(maxlen=20))
    prev = {}
    hits = []
    t = 1000.0
    for i, rc in enumerate([1, 2, 3, 4]):
        hits = detect_restart_loops(prev, _summaries(rc), events, now=t + i * 30)
    assert hits == ["crashy"]


def test_slow_restarts_do_not_trigger():
    events = defaultdict(lambda: deque(maxlen=20))
    prev = {}
    hits = []
    for i, rc in enumerate([1, 2, 3, 4]):
        hits = detect_restart_loops(prev, _summaries(rc), events, now=1000.0 + i * 400)  # >5min apart
    assert hits == []
