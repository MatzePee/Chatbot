"""Zufällige Ortszeiten bei freier Planung; punktgenaue Vorgaben bleiben exakt."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from autoposter.models import PostingPolicy
from autoposter.services import planner

TZ = ZoneInfo('Europe/Berlin')
DAY = date(2026, 11, 9)

class Channel:
    timezone = 'Europe/Berlin'


def test_one_daily_post_is_not_stuck_at_window_midpoint(monkeypatch):
    draws = iter([0.1, 0.9])
    monkeypatch.setattr(planner.random, 'random', lambda: next(draws))
    first = planner.day_slots(DAY, TZ, '09:00', '22:00', 1)[0].astimezone(TZ)
    second = planner.day_slots(DAY, TZ, '09:00', '22:00', 1)[0].astimezone(TZ)
    assert first.strftime('%H:%M') == '10:18'
    assert second.strftime('%H:%M') == '20:42'


def test_autofill_one_daily_post_also_varies_across_window(monkeypatch):
    draws = iter([0.1, 0.9])
    monkeypatch.setattr(planner.random, 'random', lambda: next(draws))
    policy = PostingPolicy(posts_per_day=1, jitter_minutes=0, stagger_minutes=0)
    window = [{'dow': list(range(7)), 'from': '09:00', 'to': '22:00'}]
    start = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    first = planner.slots_from_windows(Channel(), policy, start, 0, window, 1)[0].astimezone(TZ)
    second = planner.slots_from_windows(Channel(), policy, start, 0, window, 1)[0].astimezone(TZ)
    assert first.strftime('%H:%M') == '10:18'
    assert second.strftime('%H:%M') == '20:42'


def test_multiple_posts_are_spread_and_respect_feasible_min_gap(monkeypatch):
    draws = iter([0.9, 0.1, 0.8])
    monkeypatch.setattr(planner.random, 'random', lambda: next(draws))
    slots = planner.day_slots(DAY, TZ, '09:00', '22:00', 3, min_gap_minutes=180)
    local = [slot.astimezone(TZ) for slot in slots]
    assert len(local) == 3
    assert all(slot.date() == DAY and 9 <= slot.hour <= 22 for slot in local)
    assert all((right - left).total_seconds() >= 180 * 60
               for left, right in zip(local, local[1:]))


def test_shared_image_text_window_does_not_double_post_count(monkeypatch):
    draws = iter([0.1, 0.3, 0.6, 0.8] + [0.2, 0.8] * 10)
    monkeypatch.setattr(planner.random, 'random', lambda: next(draws))
    policy = PostingPolicy(posts_per_day=2, text_only_ratio=0.5,
                           jitter_minutes=0, stagger_minutes=0)
    start = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    slots = planner.typed_slots(Channel(), policy, start, 0)
    assert len(slots) == 2


def test_exact_time_in_manual_day_dialog_stays_exact(monkeypatch):
    monkeypatch.setattr(planner.random, 'random', lambda: 0.1)
    slot = planner.day_slots(DAY, TZ, '18:00', '18:00', 1)[0]
    assert slot.astimezone(TZ).strftime('%H:%M') == '18:00'
