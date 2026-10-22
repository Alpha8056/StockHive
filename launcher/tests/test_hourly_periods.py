"""
Checks weather_client.current_and_future_periods() with made-up NWS
hourly periods — no network calls.

Run from the launcher folder:
    venv/bin/python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import weather_client  # noqa: E402

# A fixed "now" in a -05:00 zone, 14:30 local.
TZ = timezone(timedelta(hours=-5))
NOW = datetime(2026, 7, 1, 14, 30, tzinfo=TZ)


def hourly(first_hour: int, count: int):
    """`count` one-hour periods starting at first_hour:00 local on NOW's date."""
    start = NOW.replace(hour=0, minute=0) + timedelta(hours=first_hour)
    periods = []
    for i in range(count):
        s = start + timedelta(hours=i)
        periods.append({
            "number": i + 1,
            "startTime": s.isoformat(),
            "endTime": (s + timedelta(hours=1)).isoformat(),
            "temperature": 70 + i,
        })
    return periods


class CurrentAndFuturePeriodsTest(unittest.TestCase):
    def test_all_past(self):
        self.assertEqual(weather_client.current_and_future_periods(hourly(5, 6), NOW), [])

    def test_partly_past_starts_with_current_hour(self):
        kept = weather_client.current_and_future_periods(hourly(10, 20), NOW)
        self.assertEqual(kept[0]["startTime"][11:16], "14:00")  # covers 14:30
        self.assertEqual(len(kept), 16)                         # 14:00 .. 05:00

    def test_all_future_unchanged(self):
        periods = hourly(16, 12)
        self.assertEqual(weather_client.current_and_future_periods(periods, NOW), periods)

    def test_period_ending_exactly_now_is_past(self):
        p = hourly(13, 1)
        p[0]["endTime"] = NOW.isoformat()
        self.assertEqual(weather_client.current_and_future_periods(p, NOW), [])

    def test_bad_or_missing_times_are_dropped(self):
        periods = hourly(15, 3)
        periods[0]["endTime"] = "not a time"
        del periods[1]["endTime"]
        periods[2]["endTime"] = "2026-07-01T18:00:00"  # no offset
        self.assertEqual(weather_client.current_and_future_periods(periods, NOW), [])

    def test_compares_across_time_zones(self):
        # 19:30 UTC is the same instant as 14:30 at -05:00.
        utc_now = NOW.astimezone(timezone.utc)
        kept = weather_client.current_and_future_periods(hourly(10, 20), utc_now)
        self.assertEqual(kept[0]["startTime"][11:16], "14:00")


if __name__ == "__main__":
    unittest.main()
