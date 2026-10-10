"""
Tests: Wochentage in Cron-Ausdrücken.

APScheduler 3.x zählt Wochentage 0=Montag … 6=Sonntag, Standard-Cron (und die
Beispiele im Dashboard) 0=Sonntag … 6=Samstag. Ohne Übersetzung lief
"0 9 * * 1-5" Dienstag bis Samstag statt Montag bis Freitag.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone

from scheduler_manager import build_cron_trigger, normalize_cron_weekdays

BERLIN = timezone("Europe/Berlin")
MONDAY = BERLIN.localize(datetime(2026, 10, 12))       # ein Montag
DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def fire_days(trigger: CronTrigger) -> list:
    """Wochentage, an denen der Trigger in der Woche ab MONDAY feuert."""
    found, previous, now = set(), None, MONDAY
    for _ in range(200):
        nxt = trigger.get_next_fire_time(previous, now)
        if nxt is None or nxt >= MONDAY + timedelta(days=7):
            break
        found.add(nxt.weekday())
        previous, now = nxt, nxt + timedelta(seconds=1)
    return [DAYS[d] for d in sorted(found)]


class TestLibraryBehaviour:

    def test_apscheduler_counts_monday_as_zero(self):
        """Nachweis des Fehlers: ohne Übersetzung ist "1-5" Dienstag bis Samstag."""
        raw = CronTrigger.from_crontab("0 9 * * 1-5", timezone=BERLIN)
        assert fire_days(raw) == ["Di", "Mi", "Do", "Fr", "Sa"]


class TestEachWeekday:

    @pytest.mark.parametrize("number,day", [
        ("1", "Mo"), ("2", "Di"), ("3", "Mi"), ("4", "Do"),
        ("5", "Fr"), ("6", "Sa"), ("0", "So"), ("7", "So"),
    ])
    def test_single_numeric_weekday(self, number, day):
        assert fire_days(build_cron_trigger(f"0 9 * * {number}", BERLIN)) == [day]

    @pytest.mark.parametrize("name,day", [
        ("mon", "Mo"), ("tue", "Di"), ("wed", "Mi"), ("thu", "Do"),
        ("fri", "Fr"), ("sat", "Sa"), ("sun", "So"),
    ])
    def test_named_weekday_is_unchanged(self, name, day):
        assert normalize_cron_weekdays(f"0 9 * * {name}") == f"0 9 * * {name}"
        assert fire_days(build_cron_trigger(f"0 9 * * {name}", BERLIN)) == [day]


class TestRangesAndLists:

    @pytest.mark.parametrize("expr,expected", [
        ("0 9 * * 1-5", ["Mo", "Di", "Mi", "Do", "Fr"]),
        ("0 9 * * mon-fri", ["Mo", "Di", "Mi", "Do", "Fr"]),
        ("0 10 * * 0,6", ["Sa", "So"]),
        ("0 10 * * 6,0", ["Sa", "So"]),
        ("0 10 * * 6-7", ["Sa", "So"]),
        ("0 9 * * 0-6", DAYS),
        ("0 9 * * *", DAYS),
        ("0 9 * * 1-5/2", ["Mo", "Mi", "Fr"]),
        ("0 9 * * */2", ["Di", "Do", "Sa", "So"]),
        ("0 9,15 * * 1", ["Mo"]),
    ])
    def test_fire_days(self, expr, expected):
        assert fire_days(build_cron_trigger(expr, BERLIN)) == expected

    def test_other_fields_are_untouched(self):
        assert normalize_cron_weekdays("0 8-23 1-5 * 1-5") == "0 8-23 1-5 * mon,tue,wed,thu,fri"
        assert normalize_cron_weekdays("0 0 31 2 *") == "0 0 31 2 *"

    def test_surrounding_quotes_are_tolerated(self):
        assert fire_days(build_cron_trigger('"0 9 * * 1-5"', BERLIN)) == [
            "Mo", "Di", "Mi", "Do", "Fr"]

    def test_invalid_weekday_is_still_rejected(self):
        with pytest.raises(ValueError):
            build_cron_trigger("0 9 * * 8", BERLIN)


class TestExistingSchedules:
    """Die auf dem Mac gespeicherten Zeitpläne behalten ihren gemeinten Sinn."""

    @pytest.mark.parametrize("expr,expected", [
        ("0 8,10,12,14,16 * * mon-fri", ["Mo", "Di", "Mi", "Do", "Fr"]),   # RSS, unverändert
        ("0 8-23 * * mon", ["Mo"]),                                        # Montags-Workflow
        ("0 9-17 * * 1-5", ["Mo", "Di", "Mi", "Do", "Fr"]),                # E-Mail-Jobs, korrigiert
    ])
    def test_stored_expressions(self, expr, expected):
        assert fire_days(build_cron_trigger(expr, BERLIN)) == expected

    def test_scheduler_manager_uses_the_normalised_trigger(self):
        import inspect
        import scheduler_manager
        source = inspect.getsource(scheduler_manager.SchedulerManager)
        assert "build_cron_trigger(" in source
        assert "CronTrigger.from_crontab(" not in source

    def test_validation_accepts_standard_cron_sunday(self):
        from scheduler_manager import SchedulerManager, ValidationResult
        result = ValidationResult()
        SchedulerManager._validate_cron_schedule(object.__new__(SchedulerManager),
                                                 "0 0 * * 7", result)
        assert result.valid
